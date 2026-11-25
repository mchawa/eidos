"""Tests for optional repository configuration (eidos/eidos.yaml), schema validation, precedence,
drift/validator/index enforcement, head-snapshot isolation, and context/memory/hook/plugin/portal overrides.

Covers invariants:
- INV-REPO-CONFIG-SCHEMA-AND-PRECEDENCE
- INV-DRIFT-CONFIG-ENFORCEMENT-AND-SNAPSHOT-ISOLATION
- INV-CONTEXT-AND-HOOK-CONFIG-OVERRIDES
"""

from __future__ import annotations

import glob
import io
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any

import orjson
import pytest
from typer.testing import CliRunner

from eidos.cli._shared import resolve_param_from_config
from eidos.cli.main import app
from eidos.core.index.context_memories import evaluate_memory_freshness, resolve_memory_freshness_batch
from eidos.core.index.graph_index import ArchitecturalIndex
from eidos.core.index.semantic import setup_semantic_model
from eidos.core.index.sync import check_index_dirty, sync_index
from eidos.core.lifecycle.initializer import init_repository
from eidos.core.spec.validator import SpecValidator
from eidos.core.workspace.assets import get_config_schema_path
from eidos.core.workspace.config import (
    CONFIG_ENV_VARS,
    CONFIG_FILENAME,
    DEFAULT_CONFIG,
    ConfigValidationError,
    find_config_file,
    get_config_schema_info,
    init_config_file,
    load_repo_config,
    load_repo_config_with_errors,
    load_repo_config_with_sources,
    parse_config_yaml_text,
    validate_config_data,
    validate_config_file,
)
from eidos.core.workspace.project import (
    discover_nested_subproject_roots,
    get_tracked_files,
    is_polyglot_test_file,
    load_project,
)
from eidos.plugin.hooks import capture_session_baseline, handle_stop_hook
from eidos.plugin.version import check_cli_update_status
from eidos.vcs.diff import _resolve_base_ref, classify_changed_files
from eidos.vcs.drift import detect_drift
from eidos.vcs.invariants import detect_orphan_invariants, extract_invariants_and_verify

runner = CliRunner()


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Eidos Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Eidos Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )


def _clear_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for item in CONFIG_ENV_VARS:
        monkeypatch.delenv(item["env_var"], raising=False)


def _write_minimal_governed_repo(repo: Path, *, with_git: bool = True) -> Path:
    """Create a minimal clean governed repository with system, domain, capability, container, component, and test."""
    repo.mkdir(parents=True, exist_ok=True)
    if with_git:
        _git(repo, "init", "-b", "main")
        _git(repo, "config", "user.email", "test@example.com")
        _git(repo, "config", "user.name", "Eidos Test")

    eidos_dir = repo / "eidos"
    init_repository(repo, title="Demo System")

    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "app.py").write_text(
        '"""App module."""\n\n\ndef compute_total(x: int) -> int:\n    return x + 1\n',
        encoding="utf-8",
    )
    (repo / "src" / "helper.py").write_text(
        '"""Helper module."""\n\n\ndef helper_fn() -> str:\n    return "ok"\n',
        encoding="utf-8",
    )
    (repo / "tests").mkdir(parents=True, exist_ok=True)
    (repo / "tests" / "test_app.py").write_text(
        'from src.app import compute_total\n\n\ndef test_app_invariant():\n    """Verifies INV-APP-001."""\n    assert compute_total(1) == 2\n',  # eidos:fixture
        encoding="utf-8",
    )

    (eidos_dir / "architecture" / "containers" / "app.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-APP\n"
        "title: App Container\n"
        "status: active\n"
        "container_type: library\n"
        "runtime: Python 3.12\n"
        "source_path: src\n"
        "tier: core\n"
        "domain: DOM-CORE\n"
        "---\n"
        "= Container: App\n\n"
        "== Architectural Boundary\n"
        "Owns application code under `src/`.\n",
        encoding="utf-8",
    )
    (eidos_dir / "architecture" / "components" / "app.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-APP\n"
        "title: App Component\n"
        "container: CNT-APP\n"
        "status: active\n"
        "source_path: src/app.py\n"
        "---\n"
        "= Component: App\n\n"
        "== Responsibility\n"
        "Computes totals.\n\n"
        "== Public Surface\n"
        "* `compute_total(x: int) -> int`\n",
        encoding="utf-8",
    )
    (eidos_dir / "architecture" / "components" / "helper.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-HELPER\n"
        "title: Helper Component\n"
        "container: CNT-APP\n"
        "status: active\n"
        "source_path: src/helper.py\n"
        "---\n"
        "= Component: Helper\n\n"
        "== Responsibility\n"
        "Provides helper routines.\n\n"
        "== Public Surface\n"
        "* `helper_fn() -> str`\n",
        encoding="utf-8",
    )
    (eidos_dir / "domains" / "core").mkdir(parents=True, exist_ok=True)
    (eidos_dir / "domains" / "core" / "domain.adoc").write_text(
        "---\n"
        "type: product/domain\n"
        "id: DOM-CORE\n"
        "title: Core Domain\n"
        "status: active\n"
        "classification: core\n"
        "---\n"
        "= Domain: Core\n",
        encoding="utf-8",
    )
    (eidos_dir / "domains" / "core" / "capabilities").mkdir(parents=True, exist_ok=True)
    (eidos_dir / "domains" / "core" / "capabilities" / "app.adoc").write_text(
        "---\n"
        "type: product/capability\n"
        "id: CAP-APP\n"
        "title: App Capability\n"
        "domain: DOM-CORE\n"
        "status: active\n"
        "technical:\n"
        "  containers:\n"
        "    - CNT-APP\n"
        "  components:\n"
        "    - CMP-APP\n"
        "    - CMP-HELPER\n"
        "  invariants:\n"
        "    - id: INV-APP-001\n"  # eidos:fixture
        "      rule: compute_total increments inputs deterministically.\n"
        '      test_binding: "tests/test_app.py::test_app_invariant"\n'
        "---\n"
        "= Capability: App\n\n"
        "== Intent\n"
        "Verifies `INV-APP-001`.\n",  # eidos:fixture
        encoding="utf-8",
    )

    if with_git:
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "initial clean repo")

    return eidos_dir


def test_repo_config_schema_precedence_and_subproject_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify INV-REPO-CONFIG-SCHEMA-AND-PRECEDENCE: closed Draft 2020-12 schema outside assets/schemas/v1/,
    security containment & upgrade hints, CLI > EIDOS_* env > eidos/eidos.yaml > defaults precedence,
    monorepo sub-project isolation, zero-config eidos init preservation, and eidos config CLI commands.
    """
    _clear_config_env(monkeypatch)

    # 1. Schema isolation: config schema lives outside assets/schemas/v1/ (preserving 17 canonical schemas)
    schema_path = get_config_schema_path()
    assert schema_path.is_file()
    assert schema_path.name == "eidos-config.v1.schema.yaml"
    assert "v1" not in schema_path.parts
    repo_root_real = Path(__file__).parent.parent
    v1_schemas = glob.glob(str(repo_root_real / "assets/schemas/v1/**/*.schema.yaml"), recursive=True)
    assert len(v1_schemas) == 17

    # 2. Zero-config defaults & strict <eidos_dir>/eidos.yaml placement (ignoring outer repo-root eidos.yaml)
    ws = tmp_path / "ws"
    eidos_dir = ws / "eidos"
    eidos_dir.mkdir(parents=True)
    (ws / CONFIG_FILENAME).write_text("version: 1\ndrift:\n  strict: true\n", encoding="utf-8")
    assert find_config_file(eidos_dir=eidos_dir) is None
    assert find_config_file(repo_root=ws) is None
    cfg_absent, cfg_path_absent, sources_absent = load_repo_config_with_sources(eidos_dir=eidos_dir, env={})
    assert cfg_absent == DEFAULT_CONFIG
    assert cfg_path_absent is None
    assert all(v == "default" for v in sources_absent.values())
    assert DEFAULT_CONFIG.effective_hook_strict() is False
    assert DEFAULT_CONFIG.effective_hook_fail_on_warnings() is False
    assert DEFAULT_CONFIG.context.budgets.as_dict() == {"anchor": 300, "guardrails": 1000, "deep": 2500}
    assert DEFAULT_CONFIG.memory.tier_caps.as_dict() == {"anchor": (2, 0), "guardrails": (3, 2), "deep": (5, 3)}

    # 3. Closed schema validation, future version & unknown-key upgrade hints, URL & out_dir containment
    assert validate_config_data(None) == []
    assert any("YAML mapping" in e for e in validate_config_data(["not", "a", "dict"]))

    future_errs = validate_config_data({"version": 2})
    assert any("unsupported eidos.yaml schema version 2" in e and "upgrade eidos" in e.lower() for e in future_errs)

    unknown_errs = validate_config_data({"version": 1, "unknown_section": True})
    assert any("eidos config schema" in e and "upgrade eidos" in e.lower() for e in unknown_errs)

    nested_unknown = validate_config_data({"version": 1, "drift": {"made_up_key": 1}})
    assert any("eidos config schema" in e for e in nested_unknown)

    # Disallow "all" in plugin.targets and invalid enum values
    bad_targets = validate_config_data({"version": 1, "plugin": {"targets": ["all"]}})
    assert bad_targets

    # portal.build.out_dir repo-root containment (rejects '.', abs, '..', empty, and symlink escape)
    outside_dir = tmp_path / "outside_escape"
    outside_dir.mkdir()
    escape_link = ws / "escape_link"
    escape_link.symlink_to(outside_dir)
    for bad_out in ("", ".", "./", "/tmp/abs-site", "../outside", "sub/../../outside", "escape_link/out"):
        errs = validate_config_data({"version": 1, "portal": {"build": {"out_dir": bad_out}}}, repo_root=ws)
        assert errs, f"Expected portal.build.out_dir={bad_out!r} to be rejected"
    assert validate_config_data({"version": 1, "portal": {"build": {"out_dir": "dist/portal"}}}, repo_root=ws) == []

    # context.semantic.model_mirror_url strictly http:// or https:// without credentials or whitespace
    for bad_url in (
        "",
        "file:///etc/passwd",
        "ftp://mirror.internal/harrier",
        "https://user:secret@mirror.internal/harrier",
        "https://mirror.internal/has space",
        "https://",
    ):
        errs = validate_config_data(
            {"version": 1, "context": {"semantic": {"model_mirror_url": bad_url}}}, repo_root=ws
        )
        assert errs, f"Expected model_mirror_url={bad_url!r} to be rejected"
    assert (
        validate_config_data(
            {"version": 1, "context": {"semantic": {"model_mirror_url": "https://mirror.internal/harrier"}}},
            repo_root=ws,
        )
        == []
    )
    assert (
        validate_config_data(
            {"version": 1, "context": {"semantic": {"model_mirror_url": "http://localhost:8080/harrier"}}},
            repo_root=ws,
        )
        == []
    )

    # validate_config_file error paths: missing, broken symlink, invalid UTF-8, invalid YAML
    assert validate_config_file(eidos_dir / "missing.yaml")
    broken_link = eidos_dir / "broken.yaml"
    broken_link.symlink_to(tmp_path / "does-not-exist.yaml")
    assert any("broken symbolic link" in e for e in validate_config_file(broken_link))
    bad_utf8 = eidos_dir / "bad_utf8.yaml"
    bad_utf8.write_bytes(b"\xff\xfe\x00")
    assert any("Could not read" in e for e in validate_config_file(bad_utf8))
    bad_yaml = eidos_dir / "bad_syntax.yaml"
    bad_yaml.write_text("version: [unclosed\n", encoding="utf-8")
    assert any("Invalid YAML syntax" in e for e in validate_config_file(bad_yaml))
    broken_link.unlink()
    bad_utf8.unlink()
    bad_yaml.unlink()

    # Broken symlink at canonical eidos/eidos.yaml surfaces cleanly in load_repo_config_with_errors
    canonical_cfg = eidos_dir / CONFIG_FILENAME
    canonical_cfg.symlink_to(tmp_path / "nonexistent_target.yaml")
    assert find_config_file(eidos_dir=eidos_dir) == canonical_cfg
    _, broken_errs = load_repo_config_with_errors(eidos_dir=eidos_dir, env={})
    assert any("broken symbolic link" in e for e in broken_errs)
    canonical_cfg.unlink()

    # Invalid UTF-8 & invalid YAML syntax in canonical eidos/eidos.yaml
    canonical_cfg.write_bytes(b"\xff\xfe")
    _, utf8_errs = load_repo_config_with_errors(eidos_dir=eidos_dir, env={})
    assert any("Could not read" in e for e in utf8_errs)
    canonical_cfg.write_text("drift: [bad\n", encoding="utf-8")
    _, yaml_errs = load_repo_config_with_errors(eidos_dir=eidos_dir, env={})
    assert any("Invalid YAML syntax" in e for e in yaml_errs)

    # parse_config_yaml_text invalid syntax & valid parsing
    _, txt_errs, _ = parse_config_yaml_text("version: [broken", env={})
    assert any("Invalid YAML syntax" in e for e in txt_errs)
    _, txt_schema_errs, _ = parse_config_yaml_text("version: 99\n", env={})
    assert txt_schema_errs
    parsed_cfg, ok_errs, ok_sources = parse_config_yaml_text(
        "version: 1\ndrift:\n  strict: true\n  base_ref: develop\n",
        env={"EIDOS_DRIFT_BASE_REF": "release/1.0"},
    )
    assert ok_errs == []
    assert parsed_cfg.drift.strict is True
    assert ok_sources["drift.strict"] == "file"
    assert parsed_cfg.drift.base_ref == "release/1.0"
    assert ok_sources["drift.base_ref"] == "env"

    # 4. Full valid 7-section config + EIDOS_* env precedence + fail-closed invalid env values
    full_yaml = (
        "version: 1\n"
        "workspace:\n"
        "  exclude: ['generated/**']\n"
        "  tests:\n"
        "    include: ['qa/specs/**']\n"
        "    exclude: ['tests/fixtures/**']\n"
        "  subprojects_exclude: ['examples/**']\n"
        "drift:\n"
        "  base_ref: develop\n"
        "  strict: true\n"
        "  warn_only: false\n"
        "  fail_on_warnings: true\n"
        "  ignore_paths: ['scripts/dev/**']\n"
        "  checks:\n"
        "    mermaid: warn\n"
        "    import_boundaries: warn\n"
        "    public_surface: off\n"
        "    contract_parity: warn\n"
        "    deprecated_references: error\n"
        "context:\n"
        "  default_tier: anchor\n"
        "  budgets:\n"
        "    anchor: 250\n"
        "    guardrails: 900\n"
        "    deep: 2000\n"
        "  repo_map_max_tokens: 1200\n"
        "  semantic:\n"
        "    enabled: false\n"
        "    variant: fp16\n"
        "    model_mirror_url: https://mirror.corp.example/harrier\n"
        "memory:\n"
        "  commit_drift_threshold: 3\n"
        "  tier_caps:\n"
        "    anchor: [1, 0]\n"
        "    guardrails: [2, 1]\n"
        "    deep: [4, 2]\n"
        "  default_expiry_days: 45\n"
        "lifecycle:\n"
        "  archive:\n"
        "    keep_recent: 7\n"
        "    include_explorations: false\n"
        "plugin:\n"
        "  targets: [claude, cursor]\n"
        "  launcher: uvx\n"
        "  updates_check: false\n"
        "  hooks:\n"
        "    install: true\n"
        "    enabled: true\n"
        "    strict: false\n"
        "    fail_on_warnings: false\n"
        "    baseline_ttl_seconds: 3600\n"
        "portal:\n"
        "  serve:\n"
        "    host: 0.0.0.0\n"
        "    port: 4000\n"
        "    open_browser: true\n"
        "  build:\n"
        "    out_dir: dist/portal\n"
        "    single_file: true\n"
    )
    canonical_cfg.write_text(full_yaml, encoding="utf-8")
    assert validate_config_file(canonical_cfg, repo_root=ws) == []

    loaded_cfg, loaded_path, loaded_sources = load_repo_config_with_sources(eidos_dir=eidos_dir, repo_root=ws, env={})
    assert loaded_path == canonical_cfg.resolve()
    assert loaded_cfg.workspace.exclude == ("generated/**",)
    assert loaded_cfg.workspace.tests.include == ("qa/specs/**",)
    assert loaded_cfg.workspace.tests.exclude == ("tests/fixtures/**",)
    assert loaded_cfg.workspace.subprojects_exclude == ("examples/**",)
    assert loaded_cfg.drift.base_ref == "develop"
    assert loaded_cfg.drift.checks.deprecated_references == "error"
    assert loaded_cfg.context.budgets.anchor == 250
    assert loaded_cfg.memory.default_expiry_days == 45
    assert loaded_cfg.lifecycle.archive.keep_recent == 7
    assert loaded_cfg.plugin.targets == ("claude", "cursor")
    assert loaded_cfg.effective_hook_strict() is False
    assert loaded_cfg.effective_hook_fail_on_warnings() is False
    assert loaded_cfg.portal.build.out_dir == "dist/portal"
    assert loaded_sources["drift.base_ref"] == "file"
    assert loaded_sources["portal.build.out_dir"] == "file"

    # Every EIDOS_* env var overrides file values and records "env" provenance
    env_override = {
        "EIDOS_DRIFT_BASE_REF": "origin/trunk",
        "EIDOS_DRIFT_STRICT": "0",
        "EIDOS_DRIFT_WARN_ONLY": "yes",
        "EIDOS_DRIFT_FAIL_ON_WARNINGS": "off",
        "EIDOS_CONTEXT_TIER": "DEEP",
        "EIDOS_SEMANTIC_ENABLED": "true",
        "EIDOS_SEMANTIC_VARIANT": "FP32",
        "EIDOS_SEMANTIC_MIRROR_URL": "https://env-mirror.example/harrier",
        "EIDOS_HOOKS_ENABLED": "no",
        "EIDOS_FORCE_UPDATE_CHECK": "1",
    }
    env_cfg, _, env_sources = load_repo_config_with_sources(eidos_dir=eidos_dir, repo_root=ws, env=env_override)
    assert env_cfg.drift.base_ref == "origin/trunk"
    assert env_cfg.drift.strict is False
    assert env_cfg.drift.warn_only is True
    assert env_cfg.drift.fail_on_warnings is False
    assert env_cfg.context.default_tier == "deep"
    assert env_cfg.context.semantic.enabled is True
    assert env_cfg.context.semantic.variant == "fp32"
    assert env_cfg.context.semantic.model_mirror_url == "https://env-mirror.example/harrier"
    assert env_cfg.plugin.hooks.enabled is False
    assert env_cfg.plugin.updates_check is True  # EIDOS_FORCE_UPDATE_CHECK beats file updates_check: false
    for k in (
        "drift.base_ref",
        "drift.strict",
        "drift.warn_only",
        "drift.fail_on_warnings",
        "context.default_tier",
        "context.semantic.enabled",
        "context.semantic.variant",
        "context.semantic.model_mirror_url",
        "plugin.hooks.enabled",
        "plugin.updates_check",
    ):
        assert env_sources[k] == "env"

    # EIDOS_NO_UPDATE_CHECK wins over EIDOS_FORCE_UPDATE_CHECK
    no_upd_cfg = load_repo_config(
        eidos_dir=eidos_dir,
        repo_root=ws,
        env={"EIDOS_NO_UPDATE_CHECK": "true", "EIDOS_FORCE_UPDATE_CHECK": "true"},
    )
    assert no_upd_cfg.plugin.updates_check is False

    # Fail-closed on invalid env values
    for bad_env in (
        {"EIDOS_DRIFT_STRICT": "maybe"},
        {"EIDOS_CONTEXT_TIER": "ultra"},
        {"EIDOS_SEMANTIC_ENABLED": "notabool"},
        {"EIDOS_SEMANTIC_VARIANT": "int4"},
        {"EIDOS_SEMANTIC_MIRROR_URL": "file:///local/model"},
        {"EIDOS_HOOKS_ENABLED": "nope"},
        {"EIDOS_NO_UPDATE_CHECK": "wat"},
        {"EIDOS_FORCE_UPDATE_CHECK": "wat"},
    ):
        with pytest.raises(ConfigValidationError):
            load_repo_config(eidos_dir=eidos_dir, repo_root=ws, env=bad_env)

    # 5. Monorepo sub-project isolation & workspace.subprojects_exclude
    mono = tmp_path / "monorepo"
    mono.mkdir()
    _git(mono, "init", "-b", "main")
    init_repository(mono, title="Root Mono")
    # Zero-config check: eidos init never creates eidos/eidos.yaml by default
    assert not (mono / "eidos" / CONFIG_FILENAME).exists()

    # Pre-existing eidos/eidos.yaml inside a fresh directory does not block eidos init and is preserved untouched
    pre_ws = tmp_path / "pre_existing_cfg_repo"
    (pre_ws / "eidos").mkdir(parents=True)
    pre_cfg_content = "version: 1\ndrift:\n  base_ref: develop\n"
    (pre_ws / "eidos" / CONFIG_FILENAME).write_text(pre_cfg_content, encoding="utf-8")
    init_ok, _, _ = init_repository(pre_ws, title="Pre Config Repo")
    assert init_ok is True
    assert (pre_ws / "eidos" / CONFIG_FILENAME).read_text(encoding="utf-8") == pre_cfg_content

    # Root monorepo config sets base_ref=root-main and excludes examples/**
    (mono / "eidos" / CONFIG_FILENAME).write_text(
        "version: 1\nworkspace:\n  subprojects_exclude: ['examples/**']\ndrift:\n  base_ref: root-main\n",
        encoding="utf-8",
    )
    init_repository(mono / "packages" / "svc", title="Svc")
    init_repository(mono / "examples" / "fixture", title="Example Fixture")
    (mono / "packages" / "svc" / "eidos" / CONFIG_FILENAME).write_text(
        "version: 1\ndrift:\n  base_ref: svc-develop\n  strict: true\n",
        encoding="utf-8",
    )

    nested = discover_nested_subproject_roots(mono)
    assert (mono / "packages" / "svc").resolve() in nested
    assert (mono / "examples" / "fixture").resolve() not in nested

    root_proj = load_project(mono / "eidos")
    svc_proj = load_project(mono / "packages" / "svc" / "eidos")
    assert root_proj.repo_config.drift.base_ref == "root-main"
    assert root_proj.repo_config.drift.strict is False
    assert root_proj.base_ref_candidates[0] == "root-main"
    assert svc_proj.repo_config.drift.base_ref == "svc-develop"
    assert svc_proj.repo_config.drift.strict is True
    assert svc_proj.base_ref_candidates[0] == "svc-develop"

    # 6. CLI commands: eidos config schema, show, init (Rich + --json + error paths)
    schema_info = get_config_schema_info()
    assert schema_info["schema_version"] == 1
    assert len(schema_info["sections"]) == 7
    assert len(schema_info["env_vars"]) == 11

    res_schema_rich = runner.invoke(app, ["config", "schema"])
    assert res_schema_rich.exit_code == 0
    assert "eidos/eidos.yaml" in res_schema_rich.stdout
    assert "EIDOS_DRIFT" in res_schema_rich.stdout

    res_schema_json = runner.invoke(app, ["config", "schema", "--json"])
    assert res_schema_json.exit_code == 0
    schema_payload = orjson.loads(res_schema_json.stdout)
    assert schema_payload["canonical_path"] == "eidos/eidos.yaml"
    assert len(schema_payload["sections"]) == 7
    assert any(item["env_var"] == "EIDOS_DRIFT_BASE_REF" for item in schema_payload["env_vars"])

    cli_repo = tmp_path / "cli_cfg_repo"
    init_repository(cli_repo, title="CLI Config Repo")
    cli_eidos = cli_repo / "eidos"

    # show when absent (Rich & JSON)
    res_show_absent_json = runner.invoke(app, ["config", "show", "--path", str(cli_eidos), "--json"])
    assert res_show_absent_json.exit_code == 0
    show_absent = orjson.loads(res_show_absent_json.stdout)
    assert show_absent["status"] == "absent"
    assert show_absent["config_file"] is None
    assert show_absent["sources"]["drift.strict"] == "default"
    assert runner.invoke(app, ["config", "show", "--path", str(cli_eidos)]).exit_code == 0

    # init (Rich & JSON), overwrite refusal without --force, overwrite with --force, missing dir error
    res_init_json = runner.invoke(app, ["config", "init", "--path", str(cli_eidos), "--json"])
    assert res_init_json.exit_code == 0
    assert orjson.loads(res_init_json.stdout)["created"] is True
    assert (cli_eidos / CONFIG_FILENAME).is_file()

    res_init_dup = runner.invoke(app, ["config", "init", "--path", str(cli_eidos)])
    assert res_init_dup.exit_code == 1
    res_init_dup_json = runner.invoke(app, ["config", "init", "--path", str(cli_eidos), "--json"])
    assert res_init_dup_json.exit_code == 1
    assert "already exists" in orjson.loads(res_init_dup_json.stdout)["error"]

    res_init_force = runner.invoke(app, ["config", "init", "--force", "--path", str(cli_eidos)])
    assert res_init_force.exit_code == 0

    missing_eidos = tmp_path / "no_such_eidos"
    assert runner.invoke(app, ["config", "init", "--path", str(missing_eidos), "--json"]).exit_code == 1
    with pytest.raises(FileNotFoundError):
        init_config_file(missing_eidos)

    # show when loaded with env override (Rich & JSON)
    (cli_eidos / CONFIG_FILENAME).write_text("version: 1\ndrift:\n  strict: true\n", encoding="utf-8")
    monkeypatch.setenv("EIDOS_CONTEXT_TIER", "anchor")
    res_show_loaded = runner.invoke(app, ["config", "show", "--path", str(cli_eidos), "--json"])
    assert res_show_loaded.exit_code == 0
    show_loaded = orjson.loads(res_show_loaded.stdout)
    assert show_loaded["status"] == "loaded"
    assert show_loaded["sources"]["drift.strict"] == "file"
    assert show_loaded["sources"]["context.default_tier"] == "env"
    assert runner.invoke(app, ["config", "show", "--path", str(cli_eidos)]).exit_code == 0

    # show fails closed on invalid config (Rich & JSON)
    (cli_eidos / CONFIG_FILENAME).write_text("version: 1\nbogus_key: 123\n", encoding="utf-8")
    res_show_err_json = runner.invoke(app, ["config", "show", "--path", str(cli_eidos), "--json"])
    assert res_show_err_json.exit_code == 1
    err_payload = orjson.loads(res_show_err_json.stdout)
    assert "errors" in err_payload and len(err_payload["errors"]) >= 1
    assert runner.invoke(app, ["config", "show", "--path", str(cli_eidos)]).exit_code == 1

    # resolve_param_from_config outside Click/Typer context preserves cli_value
    assert resolve_param_from_config("strict", False, True) is False
    assert resolve_param_from_config("strict", True, False) is True


def test_drift_and_validator_config_enforcement_and_head_snapshot_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify INV-DRIFT-CONFIG-ENFORCEMENT-AND-SNAPSHOT-ISOLATION:
    fail-closed SpecValidator eidos/eidos.yaml validation, all 5 drift.checks severities (error/warn/off),
    other_files routing of eidos/eidos.yaml, code-only drift.ignore_paths, workspace.exclude & workspace.tests
    across diff/invariants/sync (with repo_config_sha256 reclassification), CLI dual negation flags,
    and --head / eval_root commit-range snapshot isolation.
    """
    _clear_config_env(monkeypatch)
    repo = tmp_path / "drift_cfg_repo"
    eidos_dir = _write_minimal_governed_repo(repo, with_git=True)
    cfg_path = eidos_dir / CONFIG_FILENAME

    # 1. SpecValidator fails closed on invalid eidos/eidos.yaml
    cfg_path.write_text("version: 1\nunknown_drift_knob: true\n", encoding="utf-8")
    val_bad = SpecValidator().validate_directory(eidos_dir)
    assert val_bad.is_clean is False
    assert "eidos/eidos.yaml" in val_bad.schema_errors
    cfg_path.unlink()

    # 2. All 5 drift.checks severities: mermaid, public_surface, contract_parity, deprecated_references, import_boundaries
    # Add a deprecated policy referenced by CAP-APP, an invalid mermaid block, a public surface drift,
    # a contract parity drift, and an ARCH import boundary violation.
    (eidos_dir / "policies" / "old-policy.adoc").write_text(
        "---\n"
        "type: product/policy\n"
        "id: POL-OLD\n"
        "title: Old Policy\n"
        "status: deprecated\n"
        "category: quality\n"
        "invariants:\n"
        "  - id: INV-POL-OLD\n"  # eidos:fixture
        "    rule: Deprecated legacy rule.\n"
        "---\n"
        "= Policy: Old\n",
        encoding="utf-8",
    )
    cap_path = eidos_dir / "domains" / "core" / "capabilities" / "app.adoc"
    cap_orig = cap_path.read_text(encoding="utf-8")
    cap_path.write_text(
        cap_orig.replace(
            "technical:\n",
            "business:\n  policies:\n    - eidos/policies/old-policy.adoc\ntechnical:\n",
        )
        + "\n[mermaid]\n----\ngraph TD\n  A -->\n----\n",
        encoding="utf-8",
    )
    # Public surface phantom symbol on CMP-HELPER
    helper_spec = eidos_dir / "architecture" / "components" / "helper.adoc"
    helper_spec_orig = helper_spec.read_text(encoding="utf-8")
    helper_spec.write_text(helper_spec_orig + "* `phantom_missing_symbol() -> None`\n", encoding="utf-8")

    # Contract parity mismatch on a json_schema contract
    schema_file = repo / "src" / "payload.schema.json"
    schema_file.write_text(
        json.dumps({"type": "object", "properties": {"id": {"type": "string"}, "amount": {"type": "number"}}}),
        encoding="utf-8",
    )
    ctr_path = eidos_dir / "architecture" / "contracts" / "payload.adoc"
    ctr_path.write_text(
        "---\n"
        "type: tech/contract\n"
        "id: CTR-PAYLOAD\n"
        "title: Payload Schema\n"
        "status: active\n"
        "version: '1.0.0'\n"
        "protocol: json_schema\n"
        "container: CNT-APP\n"
        "specification_file: src/payload.schema.json\n"
        "---\n"
        "= Contract: Payload\n\n"
        "== Fields\n"
        "* `id`\n",
        encoding="utf-8",
    )

    # Default severities: mermaid=error, public_surface=error, contract_parity=error, deprecated_references=warn
    rep_err = SpecValidator().validate_directory(eidos_dir)
    assert rep_err.is_clean is False
    assert any("Mermaid" in msg for errs in rep_err.schema_errors.values() for msg in errs)
    assert any("phantom_missing_symbol" in b for b in rep_err.broken_references)
    assert any("amount" in b for b in rep_err.broken_references)
    assert any("POL-OLD" in w for w in rep_err.warnings)

    # Downgrade mermaid, public_surface, contract_parity to warn; upgrade deprecated_references to error
    cfg_path.write_text(
        "version: 1\n"
        "drift:\n"
        "  checks:\n"
        "    mermaid: warn\n"
        "    public_surface: warn\n"
        "    contract_parity: warn\n"
        "    deprecated_references: error\n",
        encoding="utf-8",
    )
    rep_mixed = SpecValidator().validate_directory(eidos_dir)
    assert rep_mixed.is_clean is False  # because deprecated_references=error
    assert any("POL-OLD" in b for b in rep_mixed.broken_references)
    assert not any("Mermaid" in msg for errs in rep_mixed.schema_errors.values() for msg in errs)
    assert any("Mermaid" in w for w in rep_mixed.warnings)
    assert any("phantom_missing_symbol" in w for w in rep_mixed.warnings)
    assert any("amount" in w for w in rep_mixed.warnings)

    # Downgrade all to warn -> is_clean is True, warnings populated; validate --fail-on-warnings vs --no-fail-on-warnings
    cfg_path.write_text(
        "version: 1\n"
        "drift:\n"
        "  fail_on_warnings: true\n"
        "  checks:\n"
        "    mermaid: warn\n"
        "    public_surface: warn\n"
        "    contract_parity: warn\n"
        "    deprecated_references: warn\n",
        encoding="utf-8",
    )
    rep_warn = SpecValidator().validate_directory(eidos_dir)
    assert rep_warn.is_clean is True
    assert len(rep_warn.warnings) >= 4
    # eidos validate honors drift.fail_on_warnings: true, overridden by --no-fail-on-warnings
    assert runner.invoke(app, ["validate", str(eidos_dir), "--json"]).exit_code == 1
    res_val_nofail = runner.invoke(app, ["validate", str(eidos_dir), "--no-fail-on-warnings", "--json"])
    assert res_val_nofail.exit_code == 0
    assert orjson.loads(res_val_nofail.stdout)["is_clean"] is True

    # Turn all 4 checks off -> 0 warnings and 0 errors
    cfg_path.write_text(
        "version: 1\n"
        "drift:\n"
        "  checks:\n"
        "    mermaid: off\n"
        "    public_surface: off\n"
        "    contract_parity: off\n"
        "    deprecated_references: off\n",
        encoding="utf-8",
    )
    rep_off = SpecValidator().validate_directory(eidos_dir)
    assert rep_off.is_clean is True
    assert rep_off.warnings == []

    # Missing source_path / specification_file on disk MUST stay blocking even when checks are off
    ctr_path.write_text(
        ctr_path.read_text(encoding="utf-8").replace("src/payload.schema.json", "src/does_not_exist.json"),
        encoding="utf-8",
    )
    rep_missing_spec = SpecValidator().validate_directory(eidos_dir)
    assert rep_missing_spec.is_clean is False
    assert any("does_not_exist.json" in b for b in rep_missing_spec.broken_references)

    # Restore clean specs before testing import_boundaries & diff/drift/snapshot
    cap_path.write_text(cap_orig, encoding="utf-8")
    helper_spec.write_text(helper_spec_orig, encoding="utf-8")
    ctr_path.unlink()
    schema_file.unlink()
    (eidos_dir / "policies" / "old-policy.adoc").unlink()
    cfg_path.unlink()

    # 3. import_boundaries check severity (error | warn | off) in both SpecValidator and detect_drift
    (eidos_dir / "architecture" / "system-architecture.adoc").write_text(
        "---\n"
        "type: tech/architecture\n"
        "id: ARCH-DEMO\n"
        "title: Demo Architecture\n"
        "status: active\n"
        "c4_context:\n"
        "  actors: []\n"
        "  external_systems: []\n"
        "import_boundaries:\n"
        "  tier_rules:\n"
        "    core:\n"
        "      allow: [core]\n"
        "---\n"
        "= Architecture\n",
        encoding="utf-8",
    )
    # CMP-HELPER denies imports from CMP-APP; add forbidden import in src/helper.py
    helper_spec.write_text(
        helper_spec_orig.replace(
            "source_path: src/helper.py\n",
            "source_path: src/helper.py\nimport_boundary:\n  deny:\n    - CMP-APP\n",
        ),
        encoding="utf-8",
    )
    (repo / "src" / "helper.py").write_text(
        '"""Helper module."""\n\nfrom src.app import compute_total\n\n\ndef helper_fn() -> str:\n    return str(compute_total(1))\n',
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "add import boundary violation")

    # Default import_boundaries: error -> detect_drift blocks
    dr_ib_err = detect_drift(repo_root=repo, eidos_dir=eidos_dir, uncommitted=True)
    assert dr_ib_err.is_clean is False
    assert len(dr_ib_err.boundary_violations) >= 1

    # import_boundaries: warn -> routed to validation_warnings, boundary_violations cleared, is_clean True
    cfg_path.write_text("version: 1\ndrift:\n  checks:\n    import_boundaries: warn\n", encoding="utf-8")
    dr_ib_warn = detect_drift(repo_root=repo, eidos_dir=eidos_dir, uncommitted=True)
    assert dr_ib_warn.is_clean is True
    assert dr_ib_warn.boundary_violations == []
    assert any("import" in w.lower() or "boundary" in w.lower() or "CMP-HELPER" in w for w in dr_ib_warn.validation_warnings)

    # import_boundaries: off -> skipped completely
    cfg_path.write_text("version: 1\ndrift:\n  checks:\n    import_boundaries: off\n", encoding="utf-8")
    dr_ib_off = detect_drift(repo_root=repo, eidos_dir=eidos_dir, uncommitted=True)
    assert dr_ib_off.is_clean is True
    assert dr_ib_off.boundary_violations == []
    assert dr_ib_off.validation_warnings == []

    # Remove forbidden import and commit clean baseline
    (repo / "src" / "helper.py").write_text(
        '"""Helper module."""\n\n\ndef helper_fn() -> str:\n    return "ok"\n',
        encoding="utf-8",
    )
    helper_spec.write_text(helper_spec_orig, encoding="utf-8")
    cfg_path.unlink()
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "clean baseline")

    # 4. classify_changed_files: eidos/eidos.yaml -> other_files (never spec_files or code_files),
    #    drift.ignore_paths -> code_files only, workspace.exclude, workspace.tests.{include,exclude}
    cfg_cls = load_repo_config(eidos_dir=eidos_dir, repo_root=repo, env={})
    assert cfg_cls == DEFAULT_CONFIG
    cfg_path.write_text(
        "version: 1\n"
        "workspace:\n"
        "  exclude: ['generated/**']\n"
        "  tests:\n"
        "    include: ['qa/specs/**']\n"
        "    exclude: ['tests/fixtures/**']\n"
        "drift:\n"
        "  ignore_paths:\n"
        "    - 'scripts/dev/**'\n"
        "    - 'eidos/**'\n"
        "    - 'tests/test_app.py'\n"
        "    - 'AGENTS.md'\n",
        encoding="utf-8",
    )
    cfg_loaded = load_repo_config(eidos_dir=eidos_dir, repo_root=repo, env={})
    classified = classify_changed_files(
        [
            "eidos/eidos.yaml",
            "eidos/system.adoc",
            "eidos/deltas/dlt-2026-001.adoc",
            "eidos/memory/mem-note.adoc",
            "AGENTS.md",
            "scripts/dev/bench.py",
            "generated/auto.py",
            "qa/specs/check_flow.py",
            "tests/fixtures/test_sample.py",
            "tests/test_app.py",
            "src/app.py",
        ],
        eidos_dir=eidos_dir,
        repo_root=repo,
        repo_config=cfg_loaded,
    )
    assert "eidos/eidos.yaml" in classified.other_files
    assert "eidos/eidos.yaml" not in classified.spec_files
    assert "eidos/eidos.yaml" not in classified.code_files
    assert "generated/auto.py" not in classified.code_files
    assert "scripts/dev/bench.py" not in classified.code_files  # suppressed by drift.ignore_paths
    # drift.ignore_paths MUST NOT suppress specs, deltas, memories, tests, or onboarding files
    assert "eidos/system.adoc" in classified.spec_files
    assert "eidos/deltas/dlt-2026-001.adoc" in classified.delta_files
    assert "eidos/memory/mem-note.adoc" in classified.memory_files
    assert "AGENTS.md" in classified.onboarding_files
    assert "tests/test_app.py" in classified.test_files
    # workspace.tests.include promotes qa/specs/**; workspace.tests.exclude demotes tests/fixtures/**
    assert "qa/specs/check_flow.py" in classified.test_files
    assert "tests/fixtures/test_sample.py" not in classified.test_files
    assert "tests/fixtures/test_sample.py" in classified.code_files
    assert is_polyglot_test_file("qa/specs/check_flow.py", repo_config=cfg_loaded) is True
    assert is_polyglot_test_file("tests/fixtures/test_sample.py", repo_config=cfg_loaded) is False

    # Editing eidos/eidos.yaml + structural code edit still triggers drift (eidos.yaml never masks code drift!)
    (repo / "src" / "app.py").write_text(
        '"""App module."""\n\n\ndef compute_total(x: int) -> int:\n    return x + 1\n\n\ndef unmapped_new_api() -> int:\n    return 42\n',
        encoding="utf-8",
    )
    dr_no_mask = detect_drift(repo_root=repo, eidos_dir=eidos_dir, uncommitted=True)
    assert dr_no_mask.drift_detected is True
    assert "eidos/eidos.yaml" in dr_no_mask.classification.other_files
    assert "eidos/eidos.yaml" not in dr_no_mask.classification.spec_files

    # Restore app.py and test CLI dual negation flags on body-only edit (--no-strict, --no-warn-only, --no-fail-on-warnings)
    (repo / "src" / "app.py").write_text(
        '"""App module."""\n\n\ndef compute_total(x: int) -> int:\n    return x + 2\n',
        encoding="utf-8",
    )
    cfg_path.write_text(
        "version: 1\n"
        "drift:\n"
        "  strict: true\n"
        "  warn_only: true\n"
        "  fail_on_warnings: true\n",
        encoding="utf-8",
    )
    # strict=true causes drift, warn_only=true makes exit code 0; --no-warn-only escalates to exit code 1!
    res_warn_only = runner.invoke(app, ["drift", "--uncommitted", "--path", str(eidos_dir), "--json"])
    assert res_warn_only.exit_code == 0
    assert orjson.loads(res_warn_only.stdout)["drift_detected"] is True
    res_no_warn = runner.invoke(app, ["drift", "--uncommitted", "--no-warn-only", "--path", str(eidos_dir), "--json"])
    assert res_no_warn.exit_code == 1
    # --no-strict overrides drift.strict: true so body-only edit is clean!
    res_no_strict = runner.invoke(
        app, ["drift", "--uncommitted", "--no-strict", "--no-warn-only", "--path", str(eidos_dir), "--json"]
    )
    assert res_no_strict.exit_code == 0
    assert orjson.loads(res_no_strict.stdout)["drift_detected"] is False

    # Restore clean working tree
    _git(repo, "checkout", "--", "src/app.py")
    cfg_path.unlink()

    # 5. workspace.exclude & workspace.tests in invariants.py (Git + non-Git) and sync.py (repo_config_sha256)
    (repo / "qa" / "specs").mkdir(parents=True, exist_ok=True)
    (repo / "qa" / "specs" / "custom_check.py").write_text(
        'def check_inv():\n    """INV-APP-001 custom qa check."""\n    return True\n',  # eidos:fixture
        encoding="utf-8",
    )
    (repo / "generated").mkdir(exist_ok=True)
    (repo / "generated" / "gen_test.py").write_text(
        'def test_gen():\n    """INV-ORPHAN-999"""\n    assert True\n',  # eidos:fixture
        encoding="utf-8",
    )
    (repo / "tests" / "fixtures").mkdir(parents=True, exist_ok=True)
    (repo / "tests" / "fixtures" / "test_excluded_fixture.py").write_text(
        'def test_fixture():\n    """INV-ORPHAN-888"""\n    assert True\n',  # eidos:fixture
        encoding="utf-8",
    )
    cfg_path.write_text(
        "version: 1\n"
        "workspace:\n"
        "  exclude: ['generated/**']\n"
        "  tests:\n"
        "    include: ['qa/specs/**']\n"
        "    exclude: ['tests/fixtures/**']\n"
        "context:\n"
        "  semantic:\n"
        "    enabled: false\n",
        encoding="utf-8",
    )
    cfg_ws = load_repo_config(eidos_dir=eidos_dir, repo_root=repo, env={})
    tracked = get_tracked_files(repo, repo_config=cfg_ws)
    assert "generated/gen_test.py" not in tracked

    # Orphans inside workspace.exclude and workspace.tests.exclude are ignored
    orphans = detect_orphan_invariants(repo, {"INV-APP-001"}, repo_config=cfg_ws)  # eidos:fixture
    assert "INV-ORPHAN-999" not in orphans  # eidos:fixture
    assert "INV-ORPHAN-888" not in orphans  # eidos:fixture

    # Non-Git os.walk fallback in invariants.py respects workspace.exclude & workspace.tests
    nongit_repo = tmp_path / "nongit_inv_repo"
    nongit_eidos = _write_minimal_governed_repo(nongit_repo, with_git=False)
    (nongit_repo / "tests" / "test_app.py").unlink()
    (nongit_repo / "qa" / "specs").mkdir(parents=True)
    (nongit_repo / "qa" / "specs" / "custom_inv.py").write_text(
        'def test_app_invariant():\n    """INV-APP-001"""\n    assert True\n',  # eidos:fixture
        encoding="utf-8",
    )
    (nongit_repo / "generated").mkdir()
    (nongit_repo / "generated" / "test_ignored.py").write_text(
        'def test_ignored():\n    """INV-GHOST-1"""\n    assert True\n',  # eidos:fixture
        encoding="utf-8",
    )
    (nongit_eidos / CONFIG_FILENAME).write_text(
        "version: 1\nworkspace:\n  exclude: ['generated/**']\n  tests:\n    include: ['qa/specs/**']\n",
        encoding="utf-8",
    )
    nongit_cap = nongit_eidos / "domains" / "core" / "capabilities" / "app.adoc"
    nongit_cap.write_text(
        nongit_cap.read_text(encoding="utf-8").replace(
            "tests/test_app.py::test_app_invariant", "qa/specs/custom_inv.py::test_app_invariant"
        ),
        encoding="utf-8",
    )
    ng_cfg = load_repo_config(eidos_dir=nongit_eidos, repo_root=nongit_repo, env={})
    ng_invs = extract_invariants_and_verify(nongit_eidos, nongit_repo, repo_config=ng_cfg)
    assert any(i.invariant_id == "INV-APP-001" and i.has_test_coverage for i in ng_invs)  # eidos:fixture
    ng_orphans = detect_orphan_invariants(nongit_repo, {"INV-APP-001"}, repo_config=ng_cfg)  # eidos:fixture
    assert "INV-GHOST-1" not in ng_orphans  # eidos:fixture

    # sync.py: repo_config_sha256 dirty detection, no indexed_files pollution, bidirectional code/source <-> code/test,
    # and context.semantic.enabled: false suppressing _trigger_auto_semantic_refresh
    (repo / "src" / "custom_qa_check.py").write_text("def check_custom():\n    return True\n", encoding="utf-8")
    (repo / "src" / "tests").mkdir(exist_ok=True)
    (repo / "src" / "tests" / "test_fixture_like.py").write_text("def test_fixture():\n    return True\n", encoding="utf-8")
    cfg_path.write_text(
        "version: 1\n"
        "workspace:\n"
        "  exclude: ['generated/**']\n"
        "  tests:\n"
        "    include: ['qa/specs/**', 'src/custom_qa_check.py']\n"
        "    exclude: ['tests/fixtures/**', 'src/tests/test_fixture_like.py']\n"
        "context:\n"
        "  semantic:\n"
        "    enabled: false\n",
        encoding="utf-8",
    )
    idx = ArchitecturalIndex(repo_root=repo, eidos_dir=eidos_dir)
    try:
        conn = idx._get_connection()
        sem_triggered: list[bool] = []
        import eidos.core.index.semantic as sem_mod

        orig_trigger = sem_mod._trigger_auto_semantic_refresh
        monkeypatch.setattr(sem_mod, "_trigger_auto_semantic_refresh", lambda *a, **kw: sem_triggered.append(True))
        sync_index(idx, force=True, auto_semantic=True)
        assert sem_triggered == []  # context.semantic.enabled: false suppressed auto semantic refresh!
        monkeypatch.setattr(sem_mod, "_trigger_auto_semantic_refresh", orig_trigger)

        # eidos/eidos.yaml is tracked in metadata['repo_config_sha256'], NOT in indexed_files
        sha_row = conn.execute("SELECT value FROM metadata WHERE key = 'repo_config_sha256'").fetchone()
        assert sha_row is not None and len(sha_row[0]) == 64
        indexed_map = {
            row["rel_path"]: row["doc_type"]
            for row in conn.execute("SELECT rel_path, doc_type FROM indexed_files").fetchall()
        }
        assert "eidos/eidos.yaml" not in indexed_map
        assert "generated/gen_test.py" not in indexed_map
        assert indexed_map.get("qa/specs/custom_check.py") == "code/test"
        assert indexed_map.get("src/custom_qa_check.py") == "code/test"
        assert indexed_map.get("src/tests/test_fixture_like.py") == "code/source"
        assert check_index_dirty(idx)[0] is False

        # Flip workspace.tests rules -> check_index_dirty detects repo_config_sha256 change and reclassifies bidirectionally
        cfg_path.write_text("version: 1\nworkspace:\n  exclude: ['generated/**']\n", encoding="utf-8")
        dirty_now, _changed, _deleted = check_index_dirty(idx)
        assert dirty_now is True
        sync_index(idx, force=False, auto_semantic=False)
        indexed_map_after = {
            row["rel_path"]: row["doc_type"]
            for row in conn.execute("SELECT rel_path, doc_type FROM indexed_files").fetchall()
        }
        assert indexed_map_after.get("src/custom_qa_check.py") == "code/source"
        assert indexed_map_after.get("src/tests/test_fixture_like.py") == "code/test"
    finally:
        idx.close()

    # Clean up extra test files before commit-range snapshot isolation test
    (repo / "src" / "custom_qa_check.py").unlink()
    (repo / "src" / "tests" / "test_fixture_like.py").unlink()
    (repo / "src" / "tests").rmdir()
    (repo / "qa" / "specs" / "custom_check.py").unlink()
    (repo / "generated" / "gen_test.py").unlink()
    (repo / "tests" / "fixtures" / "test_excluded_fixture.py").unlink()
    cfg_path.unlink()

    # 6. Commit-range --head / eval_root snapshot isolation (INV-DRIFT-CONFIG-ENFORCEMENT-AND-SNAPSHOT-ISOLATION)
    # Create a custom base branch `develop`, then a feature branch committing:
    # - drift.base_ref: develop and drift.strict: true in eidos/eidos.yaml
    # - a body-only edit to src/app.py
    _git(repo, "branch", "develop")
    _git(repo, "checkout", "-b", "feat/snapshot-test")
    cfg_path.write_text(
        "version: 1\ndrift:\n  base_ref: develop\n  strict: true\n",
        encoding="utf-8",
    )
    (repo / "src" / "app.py").write_text(
        '"""App module."""\n\n\ndef compute_total(x: int) -> int:\n    return x + 10\n',
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "commit strict config and body-only edit")

    # Verify drift.base_ref resolves develop ahead of main
    assert _resolve_base_ref(repo, repo_config=load_repo_config(eidos_dir=eidos_dir, repo_root=repo, env={})) == "develop"

    # Now poison the UNCOMMITTED working-tree eidos/eidos.yaml with strict: false AND invalid YAML!
    # Commit-range detect_drift(uncommitted=False) MUST load strictly from --head / eval_root, ignoring working tree!
    cfg_path.write_text("version: 1\ndrift:\n  strict: false\n  warn_only: true\n", encoding="utf-8")
    dr_head = detect_drift(repo_root=repo, eidos_dir=eidos_dir, base_ref=None, head_ref="HEAD", uncommitted=False)
    assert dr_head.drift_detected is True  # HEAD's strict=true enforced, working tree's strict=false ignored!
    assert "eidos/eidos.yaml" not in (dr_head.validation_errors or [])

    # CLI commit-range drift also honors HEAD's warn_only=false (exits 1), ignoring working tree's warn_only=true!
    res_cli_head = runner.invoke(app, ["drift", "--head", "HEAD", "--path", str(eidos_dir), "--json"])
    assert res_cli_head.exit_code == 1
    assert orjson.loads(res_cli_head.stdout)["drift_detected"] is True

    # Even if working-tree eidos/eidos.yaml has invalid YAML syntax, commit-range run uses HEAD's valid config cleanly!
    cfg_path.write_text("version: [broken_working_tree\n", encoding="utf-8")
    dr_head_broken_wt = detect_drift(
        repo_root=repo, eidos_dir=eidos_dir, base_ref="develop", head_ref="HEAD", uncommitted=False, strict=False
    )
    assert dr_head_broken_wt.is_clean is True


def test_context_memory_hook_plugin_and_portal_config_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify INV-CONTEXT-AND-HOOK-CONFIG-OVERRIDES:
    context defaults/budgets/repo_map_max_tokens/semantic mirror + unconditional HARRIER_ARTIFACT_MANIFEST,
    memory commit_drift_threshold/tier_caps/default_expiry_days (--expires none),
    lifecycle.archive defaults, plugin targets/launcher/updates_check/hooks (including invalid config
    non-short-circuit guard), eidos doctor checks.repo_config + missing_configured_targets advisory,
    portal serve/build defaults + dual negation flags, and eidos context never applying drift.base_ref.
    """
    _clear_config_env(monkeypatch)
    repo = tmp_path / "overrides_repo"
    eidos_dir = _write_minimal_governed_repo(repo, with_git=True)
    cfg_path = eidos_dir / CONFIG_FILENAME

    # 1. context.* overrides + eidos context explicitly NOT applying drift.base_ref
    cfg_path.write_text(
        "version: 1\n"
        "drift:\n"
        "  base_ref: nonexistent-branch-never-used-by-context\n"
        "context:\n"
        "  default_tier: anchor\n"
        "  budgets:\n"
        "    anchor: 280\n"
        "    guardrails: 950\n"
        "    deep: 2100\n"
        "  repo_map_max_tokens: 400\n"
        "  semantic:\n"
        "    enabled: false\n"
        "    variant: fp16\n"
        "    model_mirror_url: https://mirror.internal.example/harrier\n"
        "memory:\n"
        "  commit_drift_threshold: 1\n"
        "  tier_caps:\n"
        "    anchor: [1, 0]\n"
        "    guardrails: [1, 1]\n"
        "    deep: [2, 1]\n"
        "  default_expiry_days: 30\n"
        "lifecycle:\n"
        "  archive:\n"
        "    keep_recent: 2\n"
        "    include_explorations: false\n",
        encoding="utf-8",
    )

    # eidos context picks up default_tier=anchor and ignores drift.base_ref when --base is not passed
    res_ctx_default = runner.invoke(app, ["context", "-c", "CMP-APP", "--path", str(eidos_dir), "--json"])
    assert res_ctx_default.exit_code == 0
    ctx_data = orjson.loads(res_ctx_default.stdout)
    assert ctx_data["tier"] == "anchor"

    # Explicit CLI --tier deep overrides context.default_tier
    res_ctx_cli = runner.invoke(app, ["context", "-c", "CMP-APP", "--tier", "deep", "--path", str(eidos_dir), "--json"])
    assert res_ctx_cli.exit_code == 0
    assert orjson.loads(res_ctx_cli.stdout)["tier"] == "deep"

    # eidos index repo-map picks up context.repo_map_max_tokens and index sync supports --semantic/--no-semantic
    res_rm = runner.invoke(app, ["index", "repo-map", "--path", str(eidos_dir), "--json"])
    assert res_rm.exit_code == 0
    assert orjson.loads(res_rm.stdout)["max_tokens"] == 400
    res_rm_cli = runner.invoke(app, ["index", "repo-map", "--max-tokens", "777", "--path", str(eidos_dir), "--json"])
    assert res_rm_cli.exit_code == 0
    assert orjson.loads(res_rm_cli.stdout)["max_tokens"] == 777
    res_sync_no_sem = runner.invoke(app, ["index", "sync", "--path", str(eidos_dir), "--json"])
    assert res_sync_no_sem.exit_code == 0
    res_sync_sem = runner.invoke(app, ["index", "sync", "--semantic", "--path", str(eidos_dir), "--json"])
    assert res_sync_sem.exit_code == 0
    res_search = runner.invoke(app, ["index", "search", "App", "--path", str(eidos_dir), "--json"])
    assert res_search.exit_code == 0

    # 2. setup_semantic_model: resolves variant + model_mirror_url from config, unconditionally enforces
    #    HARRIER_ARTIFACT_MANIFEST SHA-256 verification (failing on mismatched mirror bytes!), and rejects file://
    class _BadMirrorResp:
        def __init__(self, data: bytes) -> None:
            self._buf = io.BytesIO(data)
            self.headers = {"Content-Length": str(len(data))}

        def read(self, n: int = -1) -> bytes:
            return self._buf.read(n)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    requested_urls: list[str] = []

    def _fake_urlopen(req: Any, timeout: int = 60) -> _BadMirrorResp:
        url_str = req.full_url if hasattr(req, "full_url") else str(req)
        requested_urls.append(url_str)
        return _BadMirrorResp(b"tampered-mirror-payload")

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    with pytest.raises(ValueError, match=r"SHA-256|Size mismatch|mismatch"):
        setup_semantic_model(eidos_dir, variant=None, force=True)
    assert any(u.startswith("https://mirror.internal.example/harrier/") for u in requested_urls)

    import hashlib

    from eidos.core.index.semantic import _METADATA_FILES, _VARIANT_FILES

    requested_urls.clear()
    payload_bytes = b"tampered-mirror-payload"
    fp16_manifest = {
        fname: (len(payload_bytes), hashlib.sha256(payload_bytes).hexdigest())
        for fname in (*_METADATA_FILES, *_VARIANT_FILES["fp16"])
    }
    res_fp16 = setup_semantic_model(eidos_dir, variant=None, force=True, expected_manifest=fp16_manifest)
    assert res_fp16["variant"] == "fp16"
    assert any("model_fp16" in u and u.startswith("https://mirror.internal.example/harrier/") for u in requested_urls)

    with pytest.raises(ValueError, match=r"http://|https://"):
        setup_semantic_model(eidos_dir, variant="quantized", base_url="file:///etc/passwd")

    # 3. memory.*: default_expiry_days + --expires none opt-out, commit_drift_threshold=1, tier_caps
    res_mem_default = runner.invoke(
        app,
        [
            "memory",
            "add",
            "--title",
            "Auto Expiring Note",
            "--summary",
            "Expires automatically from memory.default_expiry_days.",
            "--entity",
            "CMP-APP",
            "--file",
            "src/app.py",
            "--path",
            str(eidos_dir),
            "--json",
        ],
    )
    assert res_mem_default.exit_code == 0
    mem1 = orjson.loads(res_mem_default.stdout)
    assert mem1["expires_at"] is not None

    res_mem_perm = runner.invoke(
        app,
        [
            "memory",
            "add",
            "--title",
            "Permanent Note",
            "--summary",
            "Explicitly opts out of memory.default_expiry_days with --expires none.",
            "--entity",
            "CMP-APP",
            "--file",
            "src/app.py",
            "--expires",
            "none",
            "--path",
            str(eidos_dir),
            "--json",
        ],
    )
    assert res_mem_perm.exit_code == 0
    mem2 = orjson.loads(res_mem_perm.stdout)
    assert mem2["expires_at"] is None

    # Context memory tier_caps: anchor allows max 1 anchored, 0 global -> only 1 of the 2 anchored memories attaches!
    res_ctx_mem = runner.invoke(app, ["context", "-c", "CMP-APP", "--path", str(eidos_dir), "--json"])
    assert res_ctx_mem.exit_code == 0
    ctx_mems = orjson.loads(res_ctx_mem.stdout).get("memories", [])
    assert len(ctx_mems) == 1

    # Make 1 commit touching src/app.py -> commit_drift_threshold=1 marks mem1 stale automatically (with default None)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "commit memories")
    (repo / "src" / "app.py").write_text(
        '"""App module."""\n\n\ndef compute_total(x: int) -> int:\n    return x + 3\n',
        encoding="utf-8",
    )
    _git(repo, "add", "src/app.py")
    _git(repo, "commit", "-m", "touch app.py once")

    from eidos.core.spec.parser import parse_spec_file

    mem_doc = parse_spec_file(Path(mem1["path"]))
    fresh_status, _fresh_reason = evaluate_memory_freshness(
        repo, mem_doc.frontmatter, {"CMP-APP"}, eidos_dir=eidos_dir
    )
    assert fresh_status == "stale"

    batch = resolve_memory_freshness_batch(
        repo, [(mem1["id"], mem_doc.frontmatter)], {"CMP-APP"}, eidos_dir=eidos_dir
    )
    assert batch[mem1["id"]][0] == "stale"

    # memory search & memory audit honor context.semantic.enabled cleanly
    assert runner.invoke(app, ["memory", "search", "Permanent", "--path", str(eidos_dir), "--json"]).exit_code == 0
    assert runner.invoke(app, ["memory", "audit", "--path", str(eidos_dir), "--json"]).exit_code == 0

    # 4. lifecycle.archive defaults in eidos delta archive
    res_arch = runner.invoke(app, ["delta", "archive", "--dry-run", "--path", str(eidos_dir), "--json"])
    assert res_arch.exit_code == 0
    arch_data = orjson.loads(res_arch.stdout)
    assert arch_data["keep_recent"] == 2

    # 5. plugin.updates_check + EIDOS_NO_UPDATE_CHECK / EIDOS_FORCE_UPDATE_CHECK
    upd_disabled = check_cli_update_status(allow_network=False)
    assert upd_disabled["status"] == "offline"
    assert "Update check skipped" in upd_disabled["status_text"]
    assert upd_disabled["update_available"] is False

    monkeypatch.setenv("EIDOS_NO_UPDATE_CHECK", "1")
    upd_env_no = check_cli_update_status(allow_network=True)
    assert upd_env_no["status"] == "offline"
    assert "Update check skipped" in upd_env_no["status_text"]
    monkeypatch.delenv("EIDOS_NO_UPDATE_CHECK")

    # EIDOS_FORCE_UPDATE_CHECK=1 overrides allow_network=False when EIDOS_NO_UPDATE_CHECK is unset
    monkeypatch.setenv("EIDOS_FORCE_UPDATE_CHECK", "1")
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda req, timeout=2.0: _BadMirrorResp(orjson.dumps({"info": {"version": "99.0.0"}})),
    )
    upd_forced = check_cli_update_status(current_version="0.28.0", allow_network=False)
    assert upd_forced["status"] == "update_available"
    assert upd_forced["latest_version"] == "99.0.0"
    monkeypatch.delenv("EIDOS_FORCE_UPDATE_CHECK")

    # 6. plugin.targets optional [TARGET], plugin.launcher, plugin.hooks.install + --hooks/--no-hooks, required uninstall
    # Without plugin.targets configured, install/update with no TARGET exits with code 2
    res_no_target = runner.invoke(app, ["plugin", "install", "--project-dir", str(repo)])
    assert res_no_target.exit_code == 2
    res_upd_no_target = runner.invoke(app, ["plugin", "update", "--project-dir", str(repo)])
    assert res_upd_no_target.exit_code == 2
    # uninstall <TARGET> always requires TARGET (exit code 2)
    res_uninst_no_target = runner.invoke(app, ["plugin", "uninstall", "--project-dir", str(repo)])
    assert res_uninst_no_target.exit_code == 2

    # Configure concrete plugin.targets, launcher=uvx, updates_check=false, hooks.install=true
    cfg_path.write_text(
        "version: 1\n"
        "plugin:\n"
        "  targets: [claude, cursor]\n"
        "  launcher: uvx\n"
        "  updates_check: false\n"
        "  hooks:\n"
        "    install: true\n"
        "    enabled: true\n"
        "    strict: null\n"
        "    fail_on_warnings: null\n"
        "    baseline_ttl_seconds: 1\n",
        encoding="utf-8",
    )

    # eidos doctor before plugin install surfaces checks.repo_config=valid, cli_update=offline/skipped,
    # and non-blocking missing_configured_targets advisory!
    res_doc_pre = runner.invoke(app, ["doctor", "--path", str(eidos_dir), "--json"])
    assert res_doc_pre.exit_code == 0
    doc_pre = orjson.loads(res_doc_pre.stdout)
    assert doc_pre["checks"]["repo_config"]["status"] == "valid"
    assert doc_pre["checks"]["repo_config"]["ok"] is True
    assert doc_pre["checks"]["cli_update"]["status"] == "offline"
    assert doc_pre["checks"]["cli_update"]["latest_version"] is None
    assert doc_pre["checks"]["cli_update"]["update_available"] is False
    assert set(doc_pre["checks"]["agent_plugin"]["missing_configured_targets"]) == {"claude", "cursor"}
    assert runner.invoke(app, ["doctor", "--path", str(eidos_dir)]).exit_code == 0

    # Install omitting [TARGET] -> uses plugin.targets=[claude, cursor], hooks.install=true, launcher=uvx
    res_inst_cfg = runner.invoke(app, ["plugin", "install", "--project-dir", str(repo)])
    assert res_inst_cfg.exit_code == 0
    assert (repo / ".claude" / "settings.json").is_file()
    assert (repo / ".cursor" / "hooks.json").is_file()
    assert "uvx" in (repo / ".claude" / "settings.json").read_text(encoding="utf-8")

    # Update omitting [TARGET] -> updates both configured targets cleanly
    res_upd_cfg = runner.invoke(app, ["plugin", "update", "--project-dir", str(repo)])
    assert res_upd_cfg.exit_code == 0

    # Explicit --no-hooks overrides plugin.hooks.install: true on fresh target
    res_no_hooks = runner.invoke(app, ["plugin", "install", "codex", "--no-hooks", "--project-dir", str(repo)])
    assert res_no_hooks.exit_code == 0
    assert not (repo / ".codex" / "hooks.json").exists()

    # Doctor after installing claude & cursor reports zero missing_configured_targets
    res_doc_post = runner.invoke(app, ["doctor", "--path", str(eidos_dir), "--json"])
    assert res_doc_post.exit_code == 0
    assert orjson.loads(res_doc_post.stdout)["checks"]["agent_plugin"]["missing_configured_targets"] == []

    # Doctor fails closed (exit code 1) when eidos/eidos.yaml is invalid
    cfg_path.write_text("version: 1\nnot_a_key: 1\n", encoding="utf-8")
    res_doc_bad = runner.invoke(app, ["doctor", "--path", str(eidos_dir), "--json"])
    assert res_doc_bad.exit_code == 1
    assert orjson.loads(res_doc_bad.stdout)["checks"]["repo_config"]["status"] == "invalid"
    assert runner.invoke(app, ["doctor", "--path", str(eidos_dir)]).exit_code == 1

    # 7. CMP-HOOK-RUNNER: plugin.hooks.{enabled,strict,fail_on_warnings,baseline_ttl_seconds} + invalid config guard
    cfg_path.write_text(
        "version: 1\n"
        "drift:\n"
        "  strict: true\n"
        "plugin:\n"
        "  hooks:\n"
        "    enabled: false\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "commit plugin and hook config")

    # plugin.hooks.enabled: false immediately allows even with unmapped structural drift
    (repo / "src" / "app.py").write_text(
        '"""App module."""\n\n\ndef compute_total(x: int) -> int:\n    return x + 3\n\n\ndef unmapped_hook_fn() -> int:\n    return 9\n',
        encoding="utf-8",
    )
    res_hook_off = handle_stop_hook({"hook_event_name": "Stop", "session_id": "s-off"}, workspace_root=repo)
    assert res_hook_off == {}

    # Re-enable hooks with strict inherited from drift.strict (True) -> body-only edit blocks on Stop!
    _git(repo, "checkout", "--", "src/app.py")
    cfg_path.write_text(
        "version: 1\n"
        "drift:\n"
        "  strict: true\n"
        "plugin:\n"
        "  hooks:\n"
        "    enabled: true\n"
        "    strict: null\n"
        "    baseline_ttl_seconds: 10\n",
        encoding="utf-8",
    )
    _git(repo, "add", "eidos/eidos.yaml")
    _git(repo, "commit", "-m", "enable hooks strict inherit")

    capture_session_baseline({"hook_event_name": "SessionStart", "session_id": "s-strict"}, workspace_root=repo)
    (repo / "src" / "app.py").write_text(
        '"""App module."""\n\n\ndef compute_total(x: int) -> int:\n    return x + 99\n',
        encoding="utf-8",
    )
    res_hook_strict = handle_stop_hook({"hook_event_name": "Stop", "session_id": "s-strict"}, workspace_root=repo)
    assert res_hook_strict.get("decision") == "block"

    # Explicit plugin.hooks.strict: false overrides drift.strict: true -> allows body-only edit!
    cfg_path.write_text(
        "version: 1\n"
        "drift:\n"
        "  strict: true\n"
        "plugin:\n"
        "  hooks:\n"
        "    enabled: true\n"
        "    strict: false\n"
        "    baseline_ttl_seconds: 10\n",
        encoding="utf-8",
    )
    res_hook_nostrict = handle_stop_hook({"hook_event_name": "Stop", "session_id": "s-strict"}, workspace_root=repo)
    assert res_hook_nostrict == {}

    # Invalid eidos/eidos.yaml change MUST NOT zero-change short-circuit in handle_stop_hook; must block!
    _git(repo, "checkout", "--", "src/app.py", "eidos/eidos.yaml")
    capture_session_baseline({"hook_event_name": "SessionStart", "session_id": "s-invalid-cfg"}, workspace_root=repo)
    # Valid config tweak with zero governed changes short-circuits cleanly
    cfg_path.write_text(
        "version: 1\n"
        "drift:\n"
        "  strict: false\n"
        "plugin:\n"
        "  hooks:\n"
        "    enabled: true\n"
        "    baseline_ttl_seconds: 10\n",
        encoding="utf-8",
    )
    assert handle_stop_hook({"hook_event_name": "Stop", "session_id": "s-invalid-cfg"}, workspace_root=repo) == {}

    # Now corrupt eidos/eidos.yaml -> must NOT short-circuit, must block with validation error!
    cfg_path.write_text("version: 1\ninvalid_hook_key: true\n", encoding="utf-8")
    res_hook_bad_cfg = handle_stop_hook({"hook_event_name": "Stop", "session_id": "s-invalid-cfg"}, workspace_root=repo)
    assert res_hook_bad_cfg.get("decision") == "block"
    assert "eidos.yaml" in res_hook_bad_cfg.get("reason", "")

    # Verify baseline_ttl_seconds prunes stale baseline files
    cfg_path.write_text("version: 1\nplugin:\n  hooks:\n    baseline_ttl_seconds: 0.01\n", encoding="utf-8")
    baselines_dir = eidos_dir / ".cache" / "hook_baselines"
    capture_session_baseline({"hook_event_name": "SessionStart", "session_id": "s-ttl-old"}, workspace_root=repo)
    old_files = list(baselines_dir.glob("*.json"))
    assert old_files
    time.sleep(0.03)
    capture_session_baseline({"hook_event_name": "SessionStart", "session_id": "s-ttl-new"}, workspace_root=repo)
    remaining_names = {p.name for p in baselines_dir.glob("*.json")}
    assert not any(p.name in remaining_names for p in old_files)

    # 8. portal.serve.* (--open/--no-open) and portal.build.* (--single-file/--no-single-file, out_dir)
    cfg_path.write_text(
        "version: 1\n"
        "portal:\n"
        "  serve:\n"
        "    host: 0.0.0.0\n"
        "    port: 4321\n"
        "    open_browser: true\n"
        "  build:\n"
        "    out_dir: custom_portal_out\n"
        "    single_file: true\n",
        encoding="utf-8",
    )

    captured_serve: list[dict[str, Any]] = []
    captured_build: list[dict[str, Any]] = []
    import eidos.cli.portal_commands as portal_cmd_mod

    monkeypatch.setattr(
        portal_cmd_mod,
        "run_portal_server",
        lambda eidos_dir, host, port, open_browser: captured_serve.append(
            {"host": host, "port": port, "open_browser": open_browser}
        ),
    )
    monkeypatch.setattr(
        portal_cmd_mod,
        "build_static_portal",
        lambda eidos_dir, output_dir, single_file=False: captured_build.append(
            {"out_dir": Path(output_dir), "single_file": single_file}
        )
        or Path(output_dir),
    )

    assert runner.invoke(app, ["portal", "serve", "--path", str(eidos_dir)]).exit_code == 0
    assert captured_serve[-1] == {"host": "0.0.0.0", "port": 4321, "open_browser": True}

    assert runner.invoke(app, ["portal", "serve", "--path", str(eidos_dir), "--no-open", "--port", "9090"]).exit_code == 0
    assert captured_serve[-1] == {"host": "0.0.0.0", "port": 9090, "open_browser": False}

    assert runner.invoke(app, ["portal", "build", "--path", str(eidos_dir)]).exit_code == 0
    assert captured_build[-1]["out_dir"] == repo.resolve() / "custom_portal_out"
    assert captured_build[-1]["single_file"] is True

    assert (
        runner.invoke(
            app,
            ["portal", "build", "--path", str(eidos_dir), "--no-single-file", "--out", str(tmp_path / "override_out")],
        ).exit_code
        == 0
    )
    assert captured_build[-1]["out_dir"] == tmp_path / "override_out"
    assert captured_build[-1]["single_file"] is False


def test_config_cli_shared_and_sync_edge_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise load_cli_repo_config Rich/JSON error handling, custom spec-root resolution, and sync include helpers."""
    _clear_config_env(monkeypatch)
    import typer

    from eidos.cli._shared import load_cli_repo_config
    from eidos.cli.config_commands import _resolve_repo_root_for_eidos
    from eidos.core.index.sync import _compute_repo_config_sha256, _extract_test_include_targets

    # 1. load_cli_repo_config with custom non-"eidos" dir, missing dir, and Rich/JSON ConfigValidationError
    custom_repo = tmp_path / "custom_ws"
    custom_specs = custom_repo / "specs"
    custom_specs.mkdir(parents=True)
    (custom_specs / "system.adoc").write_text(
        "---\ntype: product/system\nid: SYS-CUST\ntitle: Custom\nstatus: active\nversion: '1.0.0'\n---\n= Custom\n",
        encoding="utf-8",
    )
    assert load_cli_repo_config(custom_specs) == DEFAULT_CONFIG
    assert _resolve_repo_root_for_eidos(custom_specs) == custom_repo.resolve()
    missing_specs = custom_repo / "absent_specs"
    assert load_cli_repo_config(missing_specs) == DEFAULT_CONFIG
    assert _resolve_repo_root_for_eidos(missing_specs).exists()

    (custom_specs / CONFIG_FILENAME).write_text("version: 1\nunknown_bad_key: true\n", encoding="utf-8")
    with pytest.raises(typer.Exit) as exc_rich:
        load_cli_repo_config(custom_specs, json_output=False)
    assert exc_rich.value.exit_code == 1
    with pytest.raises(typer.Exit) as exc_json:
        load_cli_repo_config(custom_specs, json_output=True)
    assert exc_json.value.exit_code == 1

    # Also invoke CLI context with invalid config to verify end-to-end exit code 1 (Rich & JSON)
    assert runner.invoke(app, ["context", "-c", "SYS-CUST", "--path", str(custom_specs)]).exit_code == 1
    res_ctx_err = runner.invoke(app, ["context", "-c", "SYS-CUST", "--path", str(custom_specs), "--json"])
    assert res_ctx_err.exit_code == 1
    assert "errors" in orjson.loads(res_ctx_err.stdout)

    # 2. _compute_repo_config_sha256 on broken symlink returns ""
    broken_eidos = tmp_path / "broken_sha_repo" / "eidos"
    broken_eidos.mkdir(parents=True)
    (broken_eidos / CONFIG_FILENAME).symlink_to(tmp_path / "nonexistent_cfg.yaml")
    assert _compute_repo_config_sha256(broken_eidos, broken_eidos.parent) == ""

    # 3. _extract_test_include_targets covers ./ prefix, empty, bare wildcard, excluded prefix/file, nested dir, literal dir & file
    root = tmp_path / "inc_root"
    (root / "qa_dir").mkdir(parents=True)
    (root / "excluded_dir").mkdir(parents=True)
    (root / "nested_sub").mkdir(parents=True)
    (root / "standalone_test.py").write_text("pass\n", encoding="utf-8")
    (root / "excluded_file.py").write_text("pass\n", encoding="utf-8")
    dirs, files = _extract_test_include_targets(
        root,
        (
            "",
            "/",
            "*.py",
            "./excluded_dir/**",
            "./qa_dir/**",
            "./qa_dir",
            "./nested_sub",
            "excluded_file.py",
            "./standalone_test.py",
        ),
        {str((root / "nested_sub").resolve())},
        ("excluded_dir/**", "excluded_file.py"),
    )
    assert str((root / "qa_dir").resolve()) in dirs
    assert str((root / "standalone_test.py").resolve()) in files
    assert str((root / "excluded_dir").resolve()) not in dirs
    assert str((root / "nested_sub").resolve()) not in dirs
    assert str((root / "excluded_file.py").resolve()) not in files

    # 4. Exercise workspace.exclude directory pruning inside sync_index / reconcile_code_ownership
    prune_repo = tmp_path / "sync_prune_repo"
    prune_eidos = _write_minimal_governed_repo(prune_repo, with_git=False)
    (prune_repo / "src" / "generated_sub").mkdir(parents=True)
    (prune_repo / "src" / "generated_sub" / "auto_gen.py").write_text("X = 1\n", encoding="utf-8")
    (prune_repo / "excluded_pkg").mkdir(parents=True)
    (prune_repo / "excluded_pkg" / "skip.py").write_text("Y = 2\n", encoding="utf-8")
    (prune_eidos / "architecture" / "containers" / "excluded.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-EXCL\n"
        "title: Excluded Container\n"
        "status: active\n"
        "container_type: library\n"
        "runtime: Python 3.12\n"
        "source_path: excluded_pkg\n"
        "tier: core\n"
        "domain: DOM-CORE\n"
        "---\n"
        "= Container: Excluded\n",
        encoding="utf-8",
    )
    (prune_eidos / CONFIG_FILENAME).write_text(
        "version: 1\nworkspace:\n  exclude: ['src/generated_sub/**', 'excluded_pkg/**']\n",
        encoding="utf-8",
    )
    prune_idx = ArchitecturalIndex(repo_root=prune_repo, eidos_dir=prune_eidos)
    try:
        sync_index(prune_idx, force=True, auto_semantic=False)
        p_conn = prune_idx._get_connection()
        rels = {r[0] for r in p_conn.execute("SELECT rel_path FROM indexed_files").fetchall()}
        assert "src/app.py" in rels
        assert "src/generated_sub/auto_gen.py" not in rels
        assert "excluded_pkg/skip.py" not in rels
        assert check_index_dirty(prune_idx)[0] is False
    finally:
        prune_idx.close()

