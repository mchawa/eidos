"""Tests verifying architectural subgraph context slicing (Initiative G)."""

import orjson
from typer.testing import CliRunner

from eidos.cli.main import app
from eidos.core.index.context import (
    ArchitecturalContextSlice,
    get_context_slice,
)

runner = CliRunner()


def test_context_slice_by_file(tmp_path, monkeypatch):
    """Verify INV-AGENT-CONTEXT-SLICING: Slicing by file path or glob pattern extracts owning component, dependencies, contracts, and invariants."""
    from pathlib import Path

    res = get_context_slice(files=["src/eidos/cli/main.py"])

    assert isinstance(res, ArchitecturalContextSlice)
    comp_ids = {c["id"] for c in res.components}
    assert "CNT-EIDOS-CLI" in comp_ids
    # 1-hop dependencies included
    assert "CNT-EIDOS-CORE" in comp_ids
    assert "CNT-EIDOS-PLUGIN" in comp_ids

    # Bound contract
    contract_ids = {c["id"] for c in res.contracts}
    assert "CTR-EIDOS-CLI" in contract_ids

    # Bound capabilities and invariants (structural tie-breaking ranks CAP-LIVING-SPEC-ENGINE and POL-GIT-SOURCE-OF-TRUTH first)
    cap_ids = {c["id"] for c in res.capabilities}
    assert "CAP-LIVING-SPEC-ENGINE" in cap_ids
    inv_ids = {i["id"] for i in res.invariants}
    assert inv_ids & {"INV-SPEC-VALIDATION", "INV-VALIDATION-WARNINGS-PORTAL-AND-CI-ESCALATION"}
    val_slice = get_context_slice(files=["src/eidos/core/spec/validator.py"])
    assert "CAP-LIVING-SPEC-ENGINE" in {c["id"] for c in val_slice.capabilities}
    assert "INV-SPEC-VALIDATION" in {i["id"] for i in val_slice.invariants}

    # Governing policies
    policy_ids = {p["id"] for p in res.policies}
    assert "POL-GIT-SOURCE-OF-TRUTH" in policy_ids

    # DLT-2026-125: guardrails/deep project domain_path, design, capability personas/policies/experience, and policy file_path
    proj = res.to_dict()
    cli_comp = proj["components"][0]
    assert cli_comp.get("domain_path", "").endswith("domain.adoc")
    cap_prod = next(c for c in proj["capabilities"] if c["id"] == "CAP-LIVING-SPEC-ENGINE")
    assert cap_prod.get("file_path", "").endswith(".adoc")
    assert cap_prod.get("personas")
    pol_entry = next(p for p in proj["policies"] if p["id"] == "POL-GIT-SOURCE-OF-TRUTH")
    assert pol_entry.get("file_path", "").endswith("git-source-of-truth.adoc")
    assert pol_entry.get("category")

    portal_sl = get_context_slice(files=["src/eidos/portal/builder.py"], tier="guardrails")
    portal_proj = portal_sl.to_dict()
    portal_comp = portal_proj["components"][0]
    design_ids = {d["id"] for d in portal_comp.get("design", [])}
    assert "DS-CORE" in design_ids
    portal_cap = portal_proj["capabilities"][0]
    assert "experience" in portal_cap
    assert portal_cap["experience"].get("screens") or portal_cap["experience"].get("design_systems")
    assert "Domain Spec:" in portal_sl.to_markdown()
    assert "**Bound Design:** `CLT-WEB-PORTAL`" in portal_sl.to_markdown()

    # Slicing directly by a design token manifest or persona/domain spec file resolves its bound architecture
    tok_sl = get_context_slice(files=["eidos/design/systems/core/tokens.json"], tier="guardrails")
    assert any(c["id"] == "CNT-EIDOS-PORTAL" for c in tok_sl.components)
    dom_file_sl = get_context_slice(files=["eidos/domains/experience-portal/domain.adoc"], tier="guardrails")
    assert any(c["id"] == "CAP-LIVING-WEB-PORTAL" for c in dom_file_sl.capabilities)

    anchor_portal = get_context_slice(files=["src/eidos/portal/builder.py"], tier="anchor").to_dict()
    assert "domain_path" not in anchor_portal["components"][0]
    assert "design" not in anchor_portal["components"][0]
    assert "provenance_tags" not in anchor_portal
    assert "provenance_tags" in proj
    assert proj["provenance_tags"]["CNT-EIDOS-CLI"]["confidence"] == "observed"
    deep_cli = get_context_slice(files=["src/eidos/cli/main.py"], tier="deep")
    deep_proj = deep_cli.to_dict()
    assert "provenance_tags" in deep_proj
    assert deep_cli.token_estimate <= 2500

    # DLT-2026-137: 1-hop ast_http frontend-to-backend expansion surfaces derived provenance_tags with file:line evidence
    http_repo = tmp_path / "http_slice_repo"
    http_eidos = http_repo / "eidos"
    (http_eidos / "architecture" / "containers").mkdir(parents=True)
    (http_repo / "apps" / "ui").mkdir(parents=True)
    (http_repo / "services" / "backend").mkdir(parents=True)
    (http_eidos / "architecture" / "containers" / "ui.adoc").write_text(
        "---\ntype: tech/container\nid: CNT-UI-SLICE\ntitle: UI Slice\nstatus: active\n"
        "container_type: web_app\nruntime: TypeScript\nsource_path: apps/ui\n---\n= UI Slice\n",
        encoding="utf-8",
    )
    (http_eidos / "architecture" / "containers" / "backend.adoc").write_text(
        "---\ntype: tech/container\nid: CNT-BACKEND-SLICE\ntitle: Backend Slice\nstatus: active\n"
        "container_type: service\nruntime: Python 3.12\nsource_path: services/backend\n---\n= Backend Slice\n",
        encoding="utf-8",
    )
    (http_repo / "apps" / "ui" / "api.ts").write_text(
        'export const fetchWidgets = () => fetch("/api/widgets");\n',
        encoding="utf-8",
    )
    (http_repo / "services" / "backend" / "app.py").write_text(
        'from fastapi import FastAPI\napp = FastAPI()\n@app.get("/api/widgets")\ndef widgets():\n    return []\n',
        encoding="utf-8",
    )
    http_sl = get_context_slice(
        files=["apps/ui/api.ts"],
        tier="guardrails",
        eidos_dir=http_eidos,
        repo_root=http_repo,
    )
    http_dict = http_sl.to_dict()
    assert http_sl.token_estimate <= 1000
    assert {"CNT-UI-SLICE", "CNT-BACKEND-SLICE"} <= {c["id"] for c in http_dict["components"]}
    assert http_dict["provenance_tags"]["CNT-UI-SLICE"]["confidence"] == "observed"
    assert http_dict["provenance_tags"]["CNT-BACKEND-SLICE"]["provenance"] == "ast_http"
    assert http_dict["provenance_tags"]["CNT-BACKEND-SLICE"]["confidence"] == "derived"
    assert "apps/ui/api.ts:1 -> GET /api/widgets" in http_dict["provenance_tags"]["CNT-BACKEND-SLICE"]["evidence"]
    assert "ast_http" in http_sl.to_markdown()

    # DLT-2026-129: deterministic --file glob slicing across existing files, planned paths, and unmatched globs
    skill_glob_sl = get_context_slice(files=["./assets/plugin_reference/skills/*/SKILL.md"], tier="guardrails")
    assert any(c["id"] == "CNT-EIDOS-PLUGIN" for c in skill_glob_sl.components)
    assert any(i["id"] == "INV-PLUGIN-SKILL-DELTA-RECONCILIATION" for i in skill_glob_sl.invariants)

    planned_glob_sl = get_context_slice(
        files=[
            "src/eidos/*/planned_future_module.py",
            "src/eidos/portal/**/planned_[a-z].py",
            "src/eidos/portal/planned_subdir/view.py",
        ],
        tier="deep",
    )
    planned_comp_ids = {c["id"] for c in planned_glob_sl.components}
    assert {"CNT-EIDOS-CLI", "CNT-EIDOS-CORE", "CNT-EIDOS-PLUGIN", "CNT-EIDOS-PORTAL"} <= planned_comp_ids

    unmatched_glob_sl = get_context_slice(files=["nonexistent_dir_xyz/*/missing.py"], tier="guardrails")
    assert unmatched_glob_sl.components == []
    assert unmatched_glob_sl.invariants == []

    # Planned cross-container glob resolved via indexed source_path prefixes before directories/files exist on disk
    mini_eidos = tmp_path / "mini_repo" / "eidos"
    mini_cnts = mini_eidos / "architecture" / "containers"
    mini_cnts.mkdir(parents=True)
    for slug, cid in (("svc-a", "CNT-SVC-A"), ("svc-b", "CNT-SVC-B")):
        (mini_cnts / f"{slug}.adoc").write_text(
            f"---\ntype: tech/container\nid: {cid}\ntitle: {cid}\nstatus: active\n"
            f"container_type: service\nruntime: Python 3.12\nsource_path: apps/{slug}/docker\n---\n= {cid}\n",
            encoding="utf-8",
        )
    mini_sl = get_context_slice(
        files=["apps/*/docker/.dockerignore", "apps/svc-a/docker/Dockerfile"],
        eidos_dir=mini_eidos,
        repo_root=tmp_path / "mini_repo",
    )
    assert {"CNT-SVC-A", "CNT-SVC-B"} <= {c["id"] for c in mini_sl.components}
    import eidos.core.index.graph_index as gi

    if gi._INDEX_INSTANCE is not None:
        gi._INDEX_INSTANCE.close()
        gi._INDEX_INSTANCE = None

    # CLI comma-separated globs, subdirectory rebasing, and missing --path error handling
    repo_root = Path(__file__).resolve().parent.parent
    missing_dir = tmp_path / "no_eidos_here"
    res_missing_json = runner.invoke(app, ["context", "--path", str(missing_dir), "--json"])
    assert res_missing_json.exit_code == 1
    assert "not found" in orjson.loads(res_missing_json.output)["error"]
    res_missing_txt = runner.invoke(app, ["context", "--path", str(missing_dir)])
    assert res_missing_txt.exit_code == 1
    assert str(missing_dir) in res_missing_txt.output
    assert "not found." in res_missing_txt.output
    assert "\n" not in res_missing_txt.output.strip()

    # Regression: 72-char (CI 'runner' boundary) and >100-char missing paths must not hard-wrap at 80 columns
    ci_boundary_path = Path("/tmp/" + ("x" * 67))  # 72 chars -> 74 chars quoted ('...' + ' not' == 78 cols)
    very_long_path = tmp_path / ("missing_" + ("y" * 100))
    for missing_p in (ci_boundary_path, very_long_path):
        res_wrap = runner.invoke(app, ["context", "--path", str(missing_p)], env={"COLUMNS": "80"})
        assert res_wrap.exit_code == 1
        assert str(missing_p) in res_wrap.output
        assert "not found." in res_wrap.output
        assert "\n" not in res_wrap.output.strip()

    monkeypatch.chdir(repo_root / "src" / "eidos" / "cli")
    res_sub_repo_glob = runner.invoke(
        app,
        ["context", "-f", "assets/plugin_reference/skills/*/SKILL.md,src/eidos/cli/main.py", "--no-semantic", "--json"],
    )
    assert res_sub_repo_glob.exit_code == 0
    sub_repo_ids = {c["id"] for c in orjson.loads(res_sub_repo_glob.output)["components"]}
    assert "CNT-EIDOS-PLUGIN" in sub_repo_ids
    assert "CNT-EIDOS-CLI" in sub_repo_ids

    res_sub_cwd_glob = runner.invoke(app, ["context", "-f", "*_command.py", "--no-semantic", "--json"])
    assert res_sub_cwd_glob.exit_code == 0
    sub_cwd_ids = {c["id"] for c in orjson.loads(res_sub_cwd_glob.output)["components"]}
    assert "CMP-CLI-CONTEXT-COMMAND" in sub_cwd_ids
    assert "CNT-EIDOS-CLI" in sub_cwd_ids

    res_outside = runner.invoke(
        app,
        [
            "context",
            "-f",
            f"{tmp_path}/*.py,{tmp_path / 'outside.py'},src/eidos/cli/../../../../outside/*.py,src/eidos/cli/../../../../outside.py",
            "--no-semantic",
            "--json",
        ],
    )
    assert res_outside.exit_code == 0
    assert orjson.loads(res_outside.output)["components"] == []

    res_abs_glob = runner.invoke(
        app, ["context", "-f", f"{repo_root}/assets/plugin_reference/skills/*/SKILL.md", "--no-semantic", "--json"]
    )
    assert res_abs_glob.exit_code == 0
    assert any(c["id"] == "CNT-EIDOS-PLUGIN" for c in orjson.loads(res_abs_glob.output)["components"])

    monkeypatch.chdir(repo_root / "src" / "eidos" / "portal")
    res_sibling_glob = runner.invoke(app, ["context", "-f", "../cli/*_command.py", "--no-semantic", "--json"])
    assert res_sibling_glob.exit_code == 0
    assert "CMP-CLI-CONTEXT-COMMAND" in {c["id"] for c in orjson.loads(res_sibling_glob.output)["components"]}

    monkeypatch.chdir(repo_root / "src" / "eidos")
    res_sub_planned_glob = runner.invoke(
        app, ["context", "-f", "./cli/planned_*.py", "--tier", "guardrails", "--no-semantic", "--json"]
    )
    assert res_sub_planned_glob.exit_code == 0
    assert "CNT-EIDOS-CLI" in {c["id"] for c in orjson.loads(res_sub_planned_glob.output)["components"]}
    monkeypatch.chdir(repo_root)

    # Slicing context_command.py retains INV-AGENT-CONTEXT-SLICING via leaf-stem focus ranking
    ctx_cmd_sl = get_context_slice(files=["src/eidos/cli/context_command.py"], tier="guardrails")
    assert any(i["id"] == "INV-AGENT-CONTEXT-SLICING" for i in ctx_cmd_sl.invariants)

    # Equal-depth wildcard source_path (src/pkg/*.py) resolves planned globs, and monorepo sub-project globs route cleanly
    wild_repo = tmp_path / "wild_repo"
    (wild_repo / "eidos" / "architecture" / "containers").mkdir(parents=True)
    (wild_repo / "eidos" / "architecture" / "containers" / "pkg.adoc").write_text(
        "---\ntype: tech/container\nid: CNT-WILD-PKG\ntitle: WildPkg\nstatus: active\n"
        "container_type: library\nruntime: Python 3.12\nsource_path: src/pkg/*.py\n---\n= WildPkg\n",
        encoding="utf-8",
    )
    wild_sl = get_context_slice(files=["src/*/planned.py"], eidos_dir=wild_repo / "eidos", repo_root=wild_repo)
    assert [c["id"] for c in wild_sl.components] == ["CNT-WILD-PKG"]
    if gi._INDEX_INSTANCE is not None:
        gi._INDEX_INSTANCE.close()
        gi._INDEX_INSTANCE = None

    mono_root = tmp_path / "mono_root"
    assert runner.invoke(app, ["init", str(mono_root), "--title", "Mono Root"]).exit_code == 0
    sub_api = mono_root / "packages" / "api"
    sub_api.mkdir(parents=True)
    assert runner.invoke(app, ["init", str(sub_api), "--title", "Sub API"]).exit_code == 0
    (sub_api / "src").mkdir()
    (sub_api / "src" / "routes.py").write_text("def route() -> None:\n    pass\n", encoding="utf-8")
    (sub_api / "eidos" / "architecture" / "containers" / "api.adoc").write_text(
        "---\ntype: tech/container\nid: CNT-SUB-API\ntitle: Sub API\nstatus: active\n"
        "container_type: service\nruntime: Python 3.12\nsource_path: src\n---\n= Sub API\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(mono_root)
    res_mono_glob = runner.invoke(app, ["context", "-f", "packages/api/src/*.py", "--no-semantic", "--json"])
    assert res_mono_glob.exit_code == 0
    assert [c["id"] for c in orjson.loads(res_mono_glob.output)["components"]] == ["CNT-SUB-API"]
    monkeypatch.chdir(repo_root)
    if gi._INDEX_INSTANCE is not None:
        gi._INDEX_INSTANCE.close()
        gi._INDEX_INSTANCE = None

    # Secondary persona and screen task expansion (lexical BM25) resolves member capabilities and containers
    idx_inst = gi.get_architectural_index()
    persona_sl = idx_inst.get_tiered_context_slice(task="PER-SOFTWARE-ARCHITECT", tier="guardrails", use_semantic=False)
    assert persona_sl.capabilities
    screen_sl = idx_inst.get_tiered_context_slice(task="SCR-OVERVIEW", tier="guardrails", use_semantic=False)
    assert screen_sl.components or screen_sl.capabilities


def test_context_slice_by_component():
    """Verify slicing by component ID extracts the target component, internal deps, and contracts."""
    res = get_context_slice(component="CNT-EIDOS-CLI")

    comp_ids = {c["id"] for c in res.components}
    assert "CNT-EIDOS-CLI" in comp_ids
    assert "CNT-EIDOS-CORE" in comp_ids

    contract_ids = {c["id"] for c in res.contracts}
    assert "CTR-EIDOS-CLI" in contract_ids


def test_context_slice_by_task():
    """Verify slicing by task description matches relevant capabilities and invariants."""
    res = get_context_slice(task="Detect and reconcile structural spec-to-code drift across Git diffs")

    cap_ids = {c["id"] for c in res.capabilities}
    assert "CAP-DRIFT-DETECTION" in cap_ids

    inv_ids = {i["id"] for i in res.invariants}
    assert "INV-DRIFT-DETECTION-ACCURACY" in inv_ids


def test_context_slice_fallback_and_empty():
    """Verify honest empty slice when explicit query matches nothing, and full catalog overview when no filters are given."""
    # Unmatched explicit query returns an honest empty slice (DLT-2026-047)
    res_unmatched = get_context_slice(component="CMP-NONEXISTENT")
    assert res_unmatched.components == []
    assert res_unmatched.policies == []
    assert res_unmatched.invariants == []

    # Empty query (no filters: all components overview)
    res_empty = get_context_slice()
    assert len(res_empty.components) >= 4


def test_context_slice_markdown_and_json_serialization():
    """Verify markdown and JSON rendering formats."""
    res = get_context_slice(files=["src/eidos/cli/main.py"], component="CNT-EIDOS-CLI")

    # Markdown
    md = res.to_markdown()
    assert "# 🧭 Eidos Architectural Context Slice" in md
    assert "C4 Level 2 Containers & Level 3 Components" in md
    assert "CNT-EIDOS-CLI" in md
    assert "Bound Interface Contracts" in md
    assert "Active Invariants" in md

    # JSON
    js_str = res.to_json()
    data = orjson.loads(js_str)
    assert "components" in data
    assert "contracts" in data
    assert "capabilities" in data
    assert "invariants" in data
    assert "policies" in data
    assert data["query"]["component"] == "CNT-EIDOS-CLI"


def test_cli_context_command():
    """Verify eidos context CLI command options and formatting."""
    # 1. Markdown output with -f
    res_f = runner.invoke(app, ["context", "-f", "src/eidos/cli/main.py"])
    assert res_f.exit_code == 0
    assert "CNT-EIDOS-CLI" in res_f.output
    assert "C4 Level 2 Containers & Level 3 Components" in res_f.output

    # 2. JSON output with -c and --json
    res_json = runner.invoke(app, ["context", "-c", "CNT-EIDOS-CLI", "--json"])
    assert res_json.exit_code == 0
    data = orjson.loads(res_json.output)
    assert any(c["id"] == "CNT-EIDOS-CLI" for c in data["components"])

    # 3. Task search with -t
    res_task = runner.invoke(app, ["context", "-t", "spec-to-code drift detection"])
    assert res_task.exit_code == 0
    assert "CAP-DRIFT-DETECTION" in res_task.output


def test_context_slice_substring_no_false_positive():
    """Verify that a file like 'packages/client/index.ts' does not match 'CNT-EIDOS-CLI' via substring 'cli'."""
    res = get_context_slice(files=["packages/client/index.ts"])
    matched_ids = {c["id"] for c in res.components}
    assert "CNT-EIDOS-CLI" not in matched_ids
    assert res.components == []


def test_context_slice_string_and_comma_separated_files():
    """Verify get_context_slice supports string inputs and comma-separated file paths."""
    res = get_context_slice(files="src/eidos/cli/main.py, src/eidos/portal/server.py")
    comp_ids = {c["id"] for c in res.components}
    assert "CNT-EIDOS-CLI" in comp_ids
    assert "CNT-EIDOS-PORTAL" in comp_ids


def test_context_slice_non_eidos_project_fallback(tmp_path):
    """Verify get_context_slice functions and provides baseline anchors in a generic non-Eidos repository."""  # eidos:fixture
    eidos_dir = tmp_path / "eidos"
    comps_dir = eidos_dir / "architecture" / "components"
    pols_dir = eidos_dir / "policies"
    comps_dir.mkdir(parents=True)
    pols_dir.mkdir(parents=True)

    (comps_dir / "auth.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-AUTH-SVC\n"
        "title: AuthService\n"
        "container: CNT-AUTH\n"
        "status: active\n"
        "source_path: src/auth\n"
        "---\n"
        "= Auth\n\n"
        "== Responsibility\n"
        "Auth service\n"
    )

    (pols_dir / "security.adoc").write_text(
        "---\n"
        "type: product/policy\n"
        "id: POL-SEC-001\n"
        "title: Security Policy\n"
        "category: security\n"
        "invariants:\n"
        "  - id: INV-NO-PASSWORD-LOG\n"
        "    rule: No password logging\n"
        "---\n"
        "= Security\n"
    )

    # Empty query fallback
    res = get_context_slice(eidos_dir=eidos_dir, repo_root=tmp_path)
    assert any(c["id"] == "CMP-AUTH-SVC" for c in res.components)
    assert any(p["id"] == "POL-SEC-001" for p in res.policies)
    # Policy invariants should be included
    assert any(i["id"] == "INV-NO-PASSWORD-LOG" for i in res.invariants)
    import eidos.core.index.graph_index as gi

    if gi._INDEX_INSTANCE is not None:
        gi._INDEX_INSTANCE.close()
        gi._INDEX_INSTANCE = None


def test_context_slice_absolute_path_normalization():
    """Verify absolute paths inside workspace resolve cleanly and do not trigger phantom matches."""
    from pathlib import Path

    abs_cli_file = str((Path.cwd() / "src/eidos/cli/main.py").resolve())
    res = get_context_slice(files=[abs_cli_file])
    comp_ids = {c["id"] for c in res.components}
    assert "CNT-EIDOS-CLI" in comp_ids
    # Should NOT falsely match CNT-EIDOS-DOCS even if ancestor paths contain words
    assert "CNT-EIDOS-DOCS" not in comp_ids


def test_context_slice_relative_path_with_dots():
    """Verify relative paths prefixed with ./ resolve cleanly."""
    res = get_context_slice(files=["./src/eidos/cli/main.py"])
    comp_ids = {c["id"] for c in res.components}
    assert "CNT-EIDOS-CLI" in comp_ids


def test_context_slice_invariants_policy_id(tmp_path):
    """Verify policy invariants in slice have policy_id assigned when defined on policies."""  # eidos:fixture
    eidos_dir = tmp_path / "eidos"
    pols_dir = eidos_dir / "policies"
    pols_dir.mkdir(parents=True)

    (pols_dir / "data-retention.adoc").write_text(
        "---\n"
        "type: product/policy\n"
        "id: POL-RET-001\n"
        "title: Data Retention Policy\n"
        "category: compliance\n"
        "invariants:\n"
        "  - id: INV-DATA-RETENTION-90D\n"
        "    rule: Purge logs older than 90 days\n"
        "---\n"
        "= Retention\n"
    )

    res = get_context_slice(eidos_dir=eidos_dir, repo_root=tmp_path)
    policy_invs = [inv for inv in res.invariants if inv.get("policy_id") == "POL-RET-001"]
    assert len(policy_invs) == 1
    assert policy_invs[0]["id"] == "INV-DATA-RETENTION-90D"
    import eidos.core.index.graph_index as gi

    if gi._INDEX_INSTANCE is not None:
        gi._INDEX_INSTANCE.close()
        gi._INDEX_INSTANCE = None


def test_cli_context_tier_option():
    """Verify eidos context supports --tier [anchor|guardrails|deep]."""
    res_anchor = runner.invoke(app, ["context", "-f", "src/eidos/cli/main.py", "--tier", "anchor"])
    assert res_anchor.exit_code == 0
    assert "ANCHOR Tier" in res_anchor.output

    res_guard = runner.invoke(app, ["context", "-f", "src/eidos/cli/main.py", "--tier", "guardrails"])
    assert res_guard.exit_code == 0
    assert "GUARDRAILS Tier" in res_guard.output

    res_deep = runner.invoke(app, ["context", "-f", "src/eidos/cli/main.py", "--tier", "deep"])
    assert res_deep.exit_code == 0
    assert "DEEP Tier" in res_deep.output


def test_cli_index_commands():
    """Verify eidos index status, sync, search, and blast-radius commands."""
    # 1. status
    res_status = runner.invoke(app, ["index", "status"])
    assert res_status.exit_code == 0
    assert "Eidos Architectural Knowledge Graph Index" in res_status.output
    assert "Entities by Type" in res_status.output

    # 2. sync (--no-semantic keeps local runs deterministic when Harrier ONNX is provisioned)
    res_sync = runner.invoke(app, ["index", "sync", "--no-semantic"])
    assert res_sync.exit_code == 0
    assert "Index is up to date" in res_sync.output or "Synchronized index" in res_sync.output

    # 3. search
    res_search = runner.invoke(app, ["index", "search", "token design system"])
    assert res_search.exit_code == 0
    assert "Architectural Search Results" in res_search.output

    # 4. blast-radius
    res_blast = runner.invoke(app, ["index", "blast-radius", "CNT-EIDOS-CORE", "--depth", "1"])
    assert res_blast.exit_code == 0
    assert "Blast Radius for 'CNT-EIDOS-CORE'" in res_blast.output

    res_blast_json = runner.invoke(app, ["index", "blast-radius", "CNT-EIDOS-CORE", "--depth", "1", "--json"])
    assert res_blast_json.exit_code == 0
    blast_data = orjson.loads(res_blast_json.output)
    assert blast_data["entity_id"] == "CNT-EIDOS-CORE"


def test_context_slice_includes_decisions_and_tiered_markdown():
    """Verify INV-AGENT-CONTEXT-SLICING: eidos context includes container decisions and rejected_alternatives in JSON and formats Markdown by tier."""
    # 1. CLI JSON output for CNT-EIDOS-INDEX
    res_json = runner.invoke(app, ["context", "--component", "CNT-EIDOS-INDEX", "--json"])
    assert res_json.exit_code == 0
    payload = orjson.loads(res_json.output)
    index_comp = next((c for c in payload["components"] if c["id"] == "CNT-EIDOS-INDEX"), None)
    assert index_comp is not None
    decisions = index_comp.get("decisions", [])
    assert any(d.get("id") == "DEC-INDEX-SQLITE-WAL" for d in decisions)
    sqlite_dec = next(d for d in decisions if d.get("id") == "DEC-INDEX-SQLITE-WAL")
    assert any("ChromaDB" in a.get("option", "") for a in sqlite_dec.get("rejected_alternatives", []))

    # 2. Tiered Markdown rendering: anchor vs guardrails vs deep
    slice_anchor = get_context_slice(component="CNT-EIDOS-INDEX", tier="anchor")
    md_anchor = slice_anchor.to_markdown()
    assert "DEC-INDEX-SQLITE-WAL" in md_anchor
    assert "ChromaDB / LanceDB Vector Store" in md_anchor
    assert "*Rationale:*" not in md_anchor

    slice_guardrails = get_context_slice(component="CNT-EIDOS-INDEX", tier="guardrails")
    md_guardrails = slice_guardrails.to_markdown()
    assert "DEC-INDEX-SQLITE-WAL" in md_guardrails
    assert "*Rationale:*" in md_guardrails
    assert "*Rejected Alternative:* **ChromaDB / LanceDB Vector Store**" in md_guardrails

    # 3. Base ArchitecturalContextSlice.to_markdown() renders embedded decisions and rejected_alternatives
    base_slice = ArchitecturalContextSlice(
        query={"component": "CNT-EIDOS-INDEX"},
        components=[index_comp],
    )
    base_md = base_slice.to_markdown()
    assert "Architectural Decisions (`DEC-*`)" in base_md
    assert "DEC-INDEX-SQLITE-WAL" in base_md
    assert "*Rejected Alternative:* **ChromaDB / LanceDB Vector Store**" in base_md


def test_inv_context_token_compression():
    """Verify INV-CONTEXT-TOKEN-COMPRESSION: tiered context slicing enforces strict token budgets across Markdown and JSON, projects tier-scoped fields in to_dict(), and returns an honest empty slice on unmatched explicit queries."""
    from eidos.core.index.graph_index import TIER_TOKEN_BUDGETS

    # 1. Enforce token budgets across anchor (<=300), guardrails (<=1000), and deep (<=2500)
    for tier, max_budget in TIER_TOKEN_BUDGETS.items():
        sl = get_context_slice(files=["src/eidos/cli/main.py"], tier=tier)
        assert sl.token_estimate <= max_budget, f"{tier} token_estimate {sl.token_estimate} > {max_budget}"
        assert len(sl.to_markdown()) // 4 <= max_budget
        assert len(sl.to_json()) // 4 <= max_budget

    # 2. Tier-scoped JSON projection in to_dict()
    anchor_dict = get_context_slice(component="CNT-EIDOS-INDEX", tier="anchor").to_dict()
    guard_dict = get_context_slice(component="CNT-EIDOS-INDEX", tier="guardrails").to_dict()
    deep_dict = get_context_slice(component="CNT-EIDOS-INDEX", tier="deep").to_dict()

    assert anchor_dict["capabilities"] == []
    assert anchor_dict["invariants"] == []
    assert anchor_dict["policies"] == []
    anchor_comp = anchor_dict["components"][0]
    assert "components" not in anchor_comp
    assert "schema" not in anchor_comp
    if anchor_comp.get("decisions"):
        assert "rationale" not in anchor_comp["decisions"][0]

    guard_comp = guard_dict["components"][0]
    assert "components" not in guard_comp
    if guard_comp.get("decisions"):
        assert "rationale" in guard_comp["decisions"][0]

    deep_comp = deep_dict["components"][0]
    assert "file_path" in deep_comp

    # 3. Unmatched explicit queries (files, component, task) return an honest empty slice
    for kwargs in (
        {"component": "CNT-DOES-NOT-EXIST-999"},
        {"files": ["nonexistent/path/that/matches/nothing.rs"]},
        {"task": "zyxwvutsrqponmlkjihgfedcba987654321"},
    ):
        empty_sl = get_context_slice(**kwargs)
        assert empty_sl.components == []
        assert empty_sl.contracts == []
        assert empty_sl.capabilities == []
        assert empty_sl.invariants == []
        assert empty_sl.policies == []
        assert empty_sl.matched_reasons == {}

    # 4. Direct task matches preserve BM25 rank order, promote owning capability/policy of matched invariants, and survive over 1-hop expansions under budget pressure
    ranked_sl = get_context_slice(
        task="agent hooks invoke native eidos hook CLI command directly without wrapper scripts",
        tier="guardrails",
    )
    assert ranked_sl.token_estimate <= TIER_TOKEN_BUDGETS["guardrails"]
    assert ranked_sl.invariants, "Expected direct invariant matches to survive guardrails token budget"
    assert ranked_sl.invariants[0]["id"] == "INV-HOOK-CLI-NATIVE"
    assert any(c["id"] == "CAP-AGENT-ADAPTER" for c in ranked_sl.capabilities)

    repo_map_sl = get_context_slice(
        task="token compression for repository structural map",
        tier="guardrails",
    )
    assert repo_map_sl.token_estimate <= TIER_TOKEN_BUDGETS["guardrails"]
    assert [i["id"] for i in repo_map_sl.invariants[:2]] == [
        "INV-INDEX-AI-REPO-MAP-BUDGET",
        "INV-CONTEXT-TOKEN-COMPRESSION",
    ]
    assert any(c["id"] == "CAP-AST-CODE-GRAPH" for c in repo_map_sl.capabilities)

    # 5. Progressive prose compression compresses verbose rules/decisions before evicting direct invariants
    from eidos.core.index.graph_index import TieredContextSlice

    synthetic_sl = TieredContextSlice(
        query={"task": "synthetic"},
        tier="guardrails",
        components=[
            {
                "id": "CNT-PRIMARY",
                "title": "Primary",
                "invariants": [
                    {
                        "id": "RULE-PRIM-1",
                        "rule": "Sentence one of primary invariant rule. " + ("Extra prose detail for primary. " * 45),
                    }
                ],
                "decisions": [
                    {
                        "id": "DEC-SYN-1",
                        "title": "Synthetic Decision",
                        "decision": "Use progressive field compression. " + ("Detailed decision clause. " * 20),
                        "rationale": "Preserves invariants under token budget pressure. "
                        + ("Detailed rationale clause. " * 20),
                    }
                ],
            },
            {
                "id": "CNT-SECONDARY",
                "title": "Secondary",
                "invariants": [
                    {
                        "id": "RULE-SEC-1",
                        "rule": "First sentence of secondary invariant rule. " + ("Verbose tail sentence. " * 45),
                    }
                ],
            },
        ],
        invariants=[
            {"id": f"RULE-SYN-{i}", "rule": f"Rule {i} must hold under budget pressure." * 4} for i in range(3)
        ],
    )
    synthetic_sl._enforce_token_budget(
        primary_cids={"CNT-PRIMARY", "CNT-SECONDARY"},
        direct_inv_ids={"RULE-SYN-0", "RULE-SYN-1", "RULE-SYN-2"},
    )
    assert synthetic_sl._compact_prose is True
    assert len(synthetic_sl.to_dict()["invariants"][0]["rule"]) <= 110
    assert len(synthetic_sl.invariants) == 3
    assert synthetic_sl.token_estimate <= TIER_TOKEN_BUDGETS["guardrails"]

    # 6. DEC-*-heavy primary container (CNT-EIDOS-PLUGIN) compacts tail DEC-* narratives in Stage 1a before
    #    evicting 1-hop capability invariants, retaining both compact DEC-* summaries and skill invariants
    for tier, max_budget in TIER_TOKEN_BUDGETS.items():
        plugin_sl = get_context_slice(files=["assets/plugin_reference/skills/*/SKILL.md"], tier=tier)
        md_out = plugin_sl.to_markdown()
        js_out = plugin_sl.to_json()
        assert plugin_sl.token_estimate <= max_budget, (
            f"{tier} token_estimate={plugin_sl.token_estimate} > {max_budget}"
        )
        assert len(md_out) // 4 <= max_budget, f"{tier} markdown tokens={len(md_out) // 4} > {max_budget}"
        assert len(js_out) // 4 <= max_budget, f"{tier} json tokens={len(js_out) // 4} > {max_budget}"
        if tier in ("guardrails", "deep"):
            proj_plugin = plugin_sl.to_dict()
            prim_decs = proj_plugin["components"][0].get("decisions", [])
            assert len(prim_decs) >= 2, f"Expected primary DEC-* summaries retained in {tier}"
            assert "DEC-PLUGIN-" in md_out
            inv_set = {inv["id"] for inv in proj_plugin["invariants"]}
            assert {"INV-PLUGIN-SKILL-DELTA-RECONCILIATION", "INV-PLUGIN-SKILL-SIZE-CEILING"} <= inv_set
            if tier == "deep":
                assert "INV-PLUGIN-SKILL-CLI-PARITY" in inv_set


def test_progressive_compression_stages_and_inline_component_context(tmp_path):
    """Verify Stage 1/2/3 eviction under extreme budget pressure, anchor overflow trimming, and inline CMP-* component context."""  # eidos:fixture
    from pathlib import Path

    import pytest

    from eidos.core.index.graph_index import TIER_TOKEN_BUDGETS, TieredContextSlice

    # 1. Stage 1 evicts non-primary 1-hop components, Stage 2 evicts non-direct invariants, Stage 3 caps direct invariants
    heavy_sl = TieredContextSlice(
        query={"task": "heavy"},
        tier="guardrails",
        components=[
            {
                "id": "CNT-PRIMARY",
                "name": "Primary",
                "responsibility": "Primary container responsibility.",
                "components": [
                    {"id": f"CMP-SUB-{i}", "name": f"Sub {i}", "responsibility": "Sub " * 25} for i in range(6)
                ],
            },
            {
                "id": "CNT-HOP1",
                "name": "Hop1",
                "responsibility": "Secondary hop responsibility " * 30,
            },
        ],
        capabilities=[{"id": f"CAP-H-{i}", "title": f"Capability {i} " * 15} for i in range(10)],
        policies=[{"id": f"POL-H-{i}", "title": f"Policy {i}", "rule": "Policy rule " * 20} for i in range(8)],
        contracts=[{"id": f"CTR-H-{i}", "title": f"Contract {i}", "protocol": "cli"} for i in range(8)],
        invariants=[{"id": f"RULE-DIRECT-{i}", "rule": f"Direct invariant rule {i} " * 15} for i in range(15)]
        + [{"id": f"RULE-INDIRECT-{i}", "rule": f"Indirect invariant rule {i} " * 15} for i in range(10)],
    )
    heavy_sl._enforce_token_budget(
        primary_cids={"CNT-PRIMARY"},
        direct_inv_ids={f"RULE-DIRECT-{i}" for i in range(15)},
    )
    assert [c["id"] for c in heavy_sl.components] == ["CNT-PRIMARY"]
    assert all(i["id"].startswith("RULE-DIRECT-") for i in heavy_sl.invariants)
    assert heavy_sl.token_estimate <= TIER_TOKEN_BUDGETS["guardrails"]

    # 2. Anchor tier overflow trims excess components and decisions to stay <= 300 tokens
    anchor_heavy = TieredContextSlice(
        query={"task": "anchor-heavy"},
        tier="anchor",
        components=[
            {
                "id": f"CNT-A-{i}",
                "name": f"Container {i}",
                "responsibility": "Long responsibility text for anchor container. " * 20,
                "decisions": [
                    {
                        "id": f"DEC-A-{i}-{j}",
                        "title": f"Decision {j}",
                        "decision": "Decision details " * 15,
                        "rejected_alternatives": [{"option": f"Alt {k}" * 10} for k in range(4)],
                    }
                    for j in range(5)
                ],
            }
            for i in range(5)
        ],
    )
    anchor_heavy._enforce_token_budget(primary_cids={"CNT-A-0"})
    assert anchor_heavy.token_estimate <= TIER_TOKEN_BUDGETS["anchor"]

    # 3. Direct query on a standalone C4 Level 3 component (CMP-CLI-APP) and base Markdown C4 L3 rendering
    inline_sl = get_context_slice(component="CMP-CLI-APP", tier="deep")
    assert any(c["id"] == "CNT-EIDOS-CLI" for c in inline_sl.components)
    assert "CMP-CLI-APP" in inline_sl.to_markdown()
    base_l3 = ArchitecturalContextSlice(
        query={"component": "CNT-EIDOS-CLI"},
        components=inline_sl.components,
        contracts=inline_sl.contracts,
        capabilities=inline_sl.capabilities,
        invariants=inline_sl.invariants,
        policies=inline_sl.policies,
        tier="deep",
    )
    base_md = base_l3.to_markdown()
    assert "[C4 Level 3 Component]" in base_md
    assert "CMP-CLI-APP" in base_md

    # 4. Workspace project.py & assets.py edge cases: _is_inside_plugin_or_assets, load_project, get_tracked_files, globs
    import subprocess

    from eidos.core.workspace.assets import (
        get_assets_path,
        get_schemas_dir,
        get_v1_schemas_dir,
    )
    from eidos.core.workspace.project import (
        _is_inside_plugin_or_assets,
        find_repo_root,
        get_tracked_files,
        load_project,
        match_path_pattern,
        matches_owned_paths,
    )

    assert _is_inside_plugin_or_assets(Path("/repo/.claude/plugins/eidos/eidos")) is True
    assert _is_inside_plugin_or_assets(Path("/repo/assets/schemas/v1")) is True
    assert get_assets_path("") == get_assets_path()
    assert get_schemas_dir().is_dir()
    assert get_v1_schemas_dir().is_dir()
    with pytest.raises(FileNotFoundError):
        get_assets_path("nonexistent_asset_subpath_999")

    # find_repo_root when start_dir is a file, and load_project with repo_root / eidos_dir
    file_in_ws = tmp_path / "git_proj" / "sub" / "app.py"
    file_in_ws.parent.mkdir(parents=True)
    file_in_ws.write_text("x = 1\n", encoding="utf-8")
    git_proj = tmp_path / "git_proj"
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=git_proj, check=True)
    subprocess.run(["git", "config", "user.email", "t@eidos.ai"], cwd=git_proj, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=git_proj, check=True)
    (git_proj / ".gitattributes").write_text("gen/** linguist-generated=true\n", encoding="utf-8")
    (git_proj / "gen").mkdir()
    (git_proj / "gen" / "bundle.js").write_text("// gen\n", encoding="utf-8")
    subprocess.run(["git", "add", "sub/app.py", ".gitattributes", "gen/bundle.js"], cwd=git_proj, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=git_proj, check=True)
    (git_proj / "untracked.py").write_text("y = 2\n", encoding="utf-8")

    assert find_repo_root(file_in_ws) == git_proj.resolve()
    tracked_and_untracked = get_tracked_files(git_proj, include_untracked=True)
    assert "sub/app.py" in tracked_and_untracked
    assert "untracked.py" in tracked_and_untracked
    assert "gen/bundle.js" not in tracked_and_untracked
    assert get_tracked_files(tmp_path / "missing_dir_999") == []

    cfg1 = load_project(repo_root=git_proj)
    assert cfg1.repo_root == git_proj.resolve()
    cfg2 = load_project(eidos_dir=git_proj / "eidos")
    assert cfg2.repo_root == git_proj.resolve()

    # Glob matching edge cases: /**/, **/, /**, **, ?, [!a-z], []a], unclosed [
    assert match_path_pattern("src/a/b/c.py", "src/**/c.py") > 0
    assert match_path_pattern("a/b/test.py", "**/test.py") > 0
    assert match_path_pattern("src/sub/mod.py", "src/**") > 0
    assert match_path_pattern("src/a.py", "**") > 0
    assert match_path_pattern("src/a1.py", "src/a?.py") > 0
    assert match_path_pattern("src/a9.py", "src/a[!a-z].py") > 0
    assert match_path_pattern("src/a].py", "src/a[]x].py") > 0
    assert match_path_pattern("src/a[.py", "src/a[.py") > 0
    assert match_path_pattern("", "src/**") == 0
    assert matches_owned_paths("src/core/app.py", ["src/core/**"])[0] is True
    assert matches_owned_paths("other/app.py", ["src/core/**"])[0] is False


def test_file_slice_compacts_before_eviction_and_respects_all_tier_budgets() -> None:  # eidos:fixture
    """Verify B1 (INV-AGENT-CONTEXT-SLICING): slicing a dogfooded file with verbose container/component prose
    compresses prose before evicting 1-hop capabilities/invariants, prioritizes capabilities binding the matched
    inline component (CAP-AST-CODE-GRAPH -> CMP-AST-SYMBOL-INDEXER), and stays within anchor/guardrails/deep budgets
    in both Markdown and JSON."""
    for tier, budget in [("anchor", 300), ("guardrails", 1000), ("deep", 2500)]:
        sl = get_context_slice(files=["src/eidos/core/index/ast_indexer.py"], tier=tier)
        md = sl.to_markdown()
        js = sl.to_json()
        assert sl.token_estimate <= budget, f"{tier} token_estimate={sl.token_estimate} > {budget}"
        assert len(md) // 4 <= budget, f"{tier} markdown tokens={len(md) // 4} > {budget}"
        assert len(js) // 4 <= budget, f"{tier} json tokens={len(js) // 4} > {budget}"

        if tier in ("guardrails", "deep"):
            cap_ids = [c["id"] for c in sl.capabilities]
            inv_ids = [i["id"] for i in sl.invariants]
            assert "CAP-AST-CODE-GRAPH" in cap_ids
            assert any("INDEX" in iid for iid in inv_ids)


def test_consolidated_entity_prefix_registry_and_excluded_dirs() -> None:  # eidos:fixture
    """Verify DLT-2026-109-CONSOLIDATE-CORE: ENTITY_PREFIX_REGISTRY, infer_entity_type_from_path, and STANDARD_EXCLUDED_DIRS."""
    from eidos.core.spec.models import (
        DOC_TYPE_TO_PREFIX,
        ENTITY_ID_PREFIXES,
        ENTITY_PREFIX_REGISTRY,
        TYPE_TO_SCHEMA_NAME,
        VALID_SPEC_TYPES,
        infer_entity_type_from_path,
        resolve_entity_prefix_meta,
    )
    from eidos.core.workspace.project import STANDARD_EXCLUDED_DIRS, is_polyglot_test_file

    for d in ("vendor", "third_party", "target", "node_modules", ".venv", ".eidos"):
        assert d in STANDARD_EXCLUDED_DIRS

    assert len(ENTITY_PREFIX_REGISTRY) == 19
    assert len(TYPE_TO_SCHEMA_NAME) == 17
    assert DOC_TYPE_TO_PREFIX["tech/component"] == "CMP-"
    assert DOC_TYPE_TO_PREFIX["agent/memory"] == "MEM-"
    assert VALID_SPEC_TYPES["ui-component"] == "design/ui-component"
    assert "INV-" in ENTITY_ID_PREFIXES
    assert resolve_entity_prefix_meta("DLT-2026-001") is None
    assert resolve_entity_prefix_meta("CNT-CORE") == ("CNT-", "container", "tech/container")

    assert infer_entity_type_from_path("eidos/architecture/components/auth.adoc") == (
        "CMP-",
        "tech/component",
        "CMP-AUTH",
    )
    assert infer_entity_type_from_path("eidos/domains/billing/domain.adoc") == (
        "DOM-",
        "product/domain",
        "DOM-BILLING",
    )
    assert infer_entity_type_from_path("eidos/design/systems/brand/system.adoc") == (
        "DS-",
        "design/system",
        "DS-BRAND",
    )
    assert infer_entity_type_from_path("eidos/design/systems/brand/components/btn.adoc") == (
        "UIC-",
        "design/ui-component",
        "UIC-BTN",
    )
    assert infer_entity_type_from_path("eidos/design/clients/web/client.adoc") == (
        "CLT-",
        "design/client",
        "CLT-WEB",
    )
    assert infer_entity_type_from_path("eidos/system.adoc") == ("SYS-", "product/system", "SYS-SYSTEM")
    assert is_polyglot_test_file("tests/test_unit.py") is True
    assert is_polyglot_test_file("src/app/test_helpers.py") is False


def test_context_brace_globs_and_csv_splitting(tmp_path, monkeypatch) -> None:
    """Verify DLT-2026-135: brace-aware CSV splitting (`split_brace_aware_csv`) and `{a,b}` brace-glob
    expansion (`expand_brace_globs`) in `eidos context -f` across repo root and nested subdirectories."""
    import orjson
    from typer.testing import CliRunner

    from eidos.cli.main import app
    from eidos.core.index.context import expand_brace_globs, split_brace_aware_csv
    from eidos.core.index.graph_index import _close_singleton_index
    from eidos.core.lifecycle.initializer import init_repository
    from eidos.core.lifecycle.scaffolder import create_spec
    from eidos.core.spec.parser import dump_spec_document, parse_spec_file

    assert split_brace_aware_csv("src/**/*.{ts,tsx},src/billing/charge.py") == [
        "src/**/*.{ts,tsx}",
        "src/billing/charge.py",
    ]
    assert expand_brace_globs("src/auth/*.{ts,tsx}") == ["src/auth/*.ts", "src/auth/*.tsx"]
    assert expand_brace_globs("pkg/{api,{v1,v2}}/*.go") == [
        "pkg/api/*.go",
        "pkg/v1/*.go",
        "pkg/v2/*.go",
    ]
    assert expand_brace_globs("src/{single}/index.ts") == ["src/{single}/index.ts"]

    repo = tmp_path / "brace_repo"
    init_repository(repo, title="brace-repo")
    eidos_dir = repo / "eidos"

    create_spec(eidos_dir, "domain", "DOM-AUTH", title="Authentication Domain")
    create_spec(eidos_dir, "domain", "DOM-BILLING", title="Billing Domain")
    cnt_auth = create_spec(eidos_dir, "container", "CNT-AUTH", title="Auth Service")
    cnt_bill = create_spec(eidos_dir, "container", "CNT-BILLING", title="Billing Service")
    cmp_auth = create_spec(eidos_dir, "component", "CMP-AUTH-WEB", title="Auth Web Handler")
    cmp_bill = create_spec(eidos_dir, "component", "CMP-BILLING-CORE", title="Billing Core")

    for spec_path, dom, src_path in (
        (cnt_auth, "DOM-AUTH", "src/auth"),
        (cnt_bill, "DOM-BILLING", "src/billing"),
    ):
        doc = parse_spec_file(spec_path)
        doc.frontmatter["domain"] = dom
        doc.frontmatter["source_path"] = src_path
        spec_path.write_text(dump_spec_document(doc.frontmatter, doc.body), encoding="utf-8")

    doc = parse_spec_file(cmp_auth)
    doc.frontmatter["container"] = "CNT-AUTH"
    doc.frontmatter["source_path"] = ["src/auth/*.ts", "src/auth/*.tsx"]
    cmp_auth.write_text(dump_spec_document(doc.frontmatter, doc.body), encoding="utf-8")

    doc = parse_spec_file(cmp_bill)
    doc.frontmatter["container"] = "CNT-BILLING"
    doc.frontmatter["source_path"] = ["src/billing/charge.py"]
    cmp_bill.write_text(dump_spec_document(doc.frontmatter, doc.body), encoding="utf-8")

    (repo / "src" / "auth").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "auth" / "login.ts").write_text(
        "export function loginUser(u: string): boolean { return !!u; }\n",
        encoding="utf-8",
    )
    (repo / "src" / "auth" / "LoginView.tsx").write_text(
        "export function LoginView() { return null; }\n",
        encoding="utf-8",
    )
    (repo / "src" / "billing").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "billing" / "charge.py").write_text(
        "def charge(cents: int) -> bool:\n    return cents > 0\n",
        encoding="utf-8",
    )

    _close_singleton_index()
    try:
        runner = CliRunner()
        ctx_res = runner.invoke(
            app,
            ["context", "-f", "src/auth/*.{ts,tsx},src/billing/charge.py", "--path", str(eidos_dir), "--json"],
        )
        assert ctx_res.exit_code == 0
        ctx_json = orjson.loads(ctx_res.stdout)
        ctx_comp_ids = {c["id"] for c in ctx_json["components"]}
        assert {"CMP-AUTH-WEB", "CMP-BILLING-CORE"} <= ctx_comp_ids

        monkeypatch.chdir(repo / "src" / "auth")
        sub_res = runner.invoke(app, ["context", "-f", "*.{ts,tsx}", "--json"])
        assert sub_res.exit_code == 0
        assert "CMP-AUTH-WEB" in {c["id"] for c in orjson.loads(sub_res.stdout)["components"]}
    finally:
        _close_singleton_index()


def test_context_uncommitted_and_base_diff_slicing(tmp_path) -> None:
    """Verify INV-CONTEXT-GIT-DIFF-SLICING: eidos context --uncommitted, --base/--head, empty diff, and invalid ref handling."""
    import subprocess

    import orjson
    from typer.testing import CliRunner

    from eidos.cli.main import app
    from eidos.core.index.graph_index import _INDEX_INSTANCES
    from eidos.core.lifecycle.initializer import init_repository
    from eidos.core.lifecycle.scaffolder import create_spec
    from eidos.core.spec.parser import dump_spec_document, parse_spec_file

    def _clear_indexes() -> None:
        for inst in list(_INDEX_INSTANCES.values()):
            inst.close()
        _INDEX_INSTANCES.clear()

    repo = tmp_path / "git_ctx_repo"
    init_repository(repo, title="GitCtx")
    eidos_dir = repo / "eidos"

    cnt_core = create_spec(eidos_dir, "container", "CNT-CORE", "Core")
    cmp_auth = create_spec(eidos_dir, "component", "CMP-AUTH", "Auth")
    cmp_bill = create_spec(eidos_dir, "component", "CMP-BILL", "Billing")

    d = parse_spec_file(cnt_core)
    d.frontmatter["status"] = "active"
    d.frontmatter["source_path"] = "src"
    cnt_core.write_text(dump_spec_document(d.frontmatter, d.body), encoding="utf-8")

    for p_cmp, sp in ((cmp_auth, "src/auth.py"), (cmp_bill, "src/billing.py")):
        dc = parse_spec_file(p_cmp)
        dc.frontmatter["status"] = "active"
        dc.frontmatter["container"] = "CNT-CORE"
        dc.frontmatter["source_path"] = sp
        p_cmp.write_text(dump_spec_document(dc.frontmatter, dc.body), encoding="utf-8")

    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "auth.py").write_text("def login() -> bool:\n    return True\n", encoding="utf-8")
    (repo / "src" / "billing.py").write_text("def charge() -> bool:\n    return True\n", encoding="utf-8")

    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    _clear_indexes()
    try:
        runner = CliRunner()

        # 1. Clean working tree -> exit 0 empty diff in both Markdown and JSON
        empty_md = runner.invoke(app, ["context", "--uncommitted", "--path", str(eidos_dir)])
        assert empty_md.exit_code == 0
        assert "No changed files detected" in empty_md.stdout

        empty_json = runner.invoke(app, ["context", "--uncommitted", "--path", str(eidos_dir), "--json"])
        assert empty_json.exit_code == 0
        assert orjson.loads(empty_json.stdout)["components"] == []

        # 2. Uncommitted modification to src/auth.py slices CMP-AUTH automatically
        (repo / "src" / "auth.py").write_text("def login() -> bool:\n    return False\n", encoding="utf-8")
        uncomm_res = runner.invoke(app, ["context", "-u", "--path", str(eidos_dir), "--json"])
        assert uncomm_res.exit_code == 0
        uncomm_ids = {c["id"] for c in orjson.loads(uncomm_res.stdout)["components"]}
        assert "CMP-AUTH" in uncomm_ids
        assert "CMP-BILL" not in uncomm_ids

        # 3. Commit on feature branch and slice with --base main --head HEAD
        subprocess.run(["git", "checkout", "-b", "feat/billing"], cwd=repo, check=True, capture_output=True)
        (repo / "src" / "billing.py").write_text("def charge() -> bool:\n    return False\n", encoding="utf-8")
        subprocess.run(["git", "add", "src/auth.py", "src/billing.py"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "update both"], cwd=repo, check=True, capture_output=True)

        base_res = runner.invoke(
            app, ["context", "--base", "main", "--head", "HEAD", "--path", str(eidos_dir), "--json"]
        )
        assert base_res.exit_code == 0
        base_ids = {c["id"] for c in orjson.loads(base_res.stdout)["components"]}
        assert {"CMP-AUTH", "CMP-BILL"} <= base_ids

        # 4. Unresolvable --base ref fails closed with exit code 1 in both JSON and text modes
        bad_json = runner.invoke(app, ["context", "--base", "nonexistent-ref-xyz", "--path", str(eidos_dir), "--json"])
        assert bad_json.exit_code == 1
        assert "error" in orjson.loads(bad_json.stdout)

        bad_txt = runner.invoke(app, ["context", "--base", "nonexistent-ref-xyz", "--path", str(eidos_dir)])
        assert bad_txt.exit_code == 1
    finally:
        _clear_indexes()


def test_context_guardrail_retains_invariants_within_budget_headroom(tmp_path):
    """Verify INV-CONTEXT-GUARDRAIL-INVARIANT-RETENTION: live-repo catalog-agnostic guardrails smoke check, hermetic AST test-to-source coupling and ID specificity ranking precedence, zero code_couplings pollution, metadata backfill, and secondary compaction headroom."""
    from eidos.core.index.code_graph import build_module_to_file_map, resolve_import_target_file
    from eidos.core.index.graph_index import TIER_TOKEN_BUDGETS, ArchitecturalIndex, TieredContextSlice
    from eidos.core.lifecycle.initializer import init_repository
    from eidos.core.lifecycle.scaffolder import create_spec
    from eidos.core.spec.parser import dump_spec_document, parse_spec_file

    # 1. Live-repo smoke test (strictly catalog-agnostic properties so catalog growth never causes brittle failures)
    cli_slice = get_context_slice(files=["src/eidos/cli/main.py"], tier="guardrails")
    assert cli_slice.token_estimate <= TIER_TOKEN_BUDGETS["guardrails"]
    assert len(cli_slice.to_markdown()) // 4 <= TIER_TOKEN_BUDGETS["guardrails"]
    assert len(cli_slice.to_json()) // 4 <= TIER_TOKEN_BUDGETS["guardrails"]
    assert {"CMP-CLI-APP", "CNT-EIDOS-CLI"} <= {c["id"] for c in cli_slice.components}
    assert any(c["id"] == "CTR-EIDOS-CLI" for c in cli_slice.contracts)
    assert any(
        c["id"] in {"CAP-LIVING-SPEC-ENGINE", "CAP-EXPLORATION-DISCOVERY", "CAP-SPEC-DELTA-MUTATIONS"}
        for c in cli_slice.capabilities
    )
    assert len(cli_slice.invariants) >= 3, (
        f"Expected >= 3 retained capability invariants on guardrails slice, got {[i['id'] for i in cli_slice.invariants]}"
    )

    # 2. Hermetic synthetic repo: verify exact ranking precedence:
    #    CMP-*.invariants > AST test coupling (edited file > primary CMP-* sibling) >
    #    lexical ID specificity (1/2 = 0.50 > 1/5 = 0.20) > declaration order (not displaced by incidental test_binding)
    inv_pfx = "INV"
    id_explicit = f"{inv_pfx}-CMP-EXPLICIT"
    id_foundational = f"{inv_pfx}-FOUNDATIONAL-BASE"
    id_incidental = f"{inv_pfx}-INCIDENTAL-TB-ONLY"
    id_broad = f"{inv_pfx}-BROAD-CLI-OPENAPI-SCHEMA-PARITY"
    id_narrow = f"{inv_pfx}-CLI-PARITY"
    id_sibling = f"{inv_pfx}-SIBLING-IMPORT-COUPLED"
    id_edited = f"{inv_pfx}-EDITED-IMPORT-COUPLED"
    id_tbd_coupled = f"{inv_pfx}-DISCOVERED-COUPLED"

    repo = tmp_path / "ast_rank_repo"
    init_repository(repo, title="AstRank")
    eidos_dir = repo / "eidos"

    cnt_app = create_spec(eidos_dir, "container", "CNT-APP", "App Container")
    cmp_cli = create_spec(eidos_dir, "component", "CMP-CLI", "CLI Component")
    dom_core = create_spec(eidos_dir, "domain", "DOM-CORE", "Core Domain")
    cap_app = create_spec(eidos_dir, "capability", "CAP-APP-001", "CLI Capability")

    d_dom = parse_spec_file(dom_core)
    d_dom.frontmatter["status"] = "active"
    dom_core.write_text(dump_spec_document(d_dom.frontmatter, d_dom.body), encoding="utf-8")

    d_cnt = parse_spec_file(cnt_app)
    d_cnt.frontmatter["status"] = "active"
    d_cnt.frontmatter["domain"] = "DOM-CORE"
    d_cnt.frontmatter["source_path"] = "src/app"
    cnt_app.write_text(dump_spec_document(d_cnt.frontmatter, d_cnt.body), encoding="utf-8")

    d_cmp = parse_spec_file(cmp_cli)
    d_cmp.frontmatter["status"] = "active"
    d_cmp.frontmatter["container"] = "CNT-APP"
    d_cmp.frontmatter["source_path"] = ["src/app/cli/main.py", "src/app/cli/helpers.py"]
    d_cmp.frontmatter["invariants"] = [
        {
            "id": id_explicit,
            "rule": "Explicit CMP-CLI invariant always ranks ahead of capability invariants.",
            "test_binding": "tests/test_unrelated.py::test_explicit",
        }
    ]
    cmp_cli.write_text(dump_spec_document(d_cmp.frontmatter, d_cmp.body), encoding="utf-8")

    d_cap = parse_spec_file(cap_app)
    d_cap.frontmatter["status"] = "active"
    d_cap.frontmatter["domain"] = "DOM-CORE"
    d_cap.frontmatter["technical"] = {
        "containers": ["CNT-APP"],
        "components": ["CMP-CLI"],
        "invariants": [
            {
                "id": id_foundational,
                "rule": "Foundational capability invariant declared first.",
                "test_binding": "tests/test_unrelated.py::test_foundational",
            },
            {
                "id": id_incidental,
                "rule": "Unrelated invariant whose test function name happens to contain cli.",
                "test_binding": "tests/test_unrelated.py::test_cli_incidental_noise",
            },
            {
                "id": id_broad,
                "rule": "Broad multi-topic invariant with 1/5 = 0.20 ID token specificity on cli.",
                "test_binding": "tests/test_subproc_broad.py::test_broad",
            },
            {
                "id": id_narrow,
                "rule": "Focused invariant with 1/2 = 0.50 ID token specificity on cli.",
                "test_binding": "tests/test_subproc_narrow.py::test_narrow",
            },
            {
                "id": id_sibling,
                "rule": "Invariant whose test imports sibling file helpers.py in primary CMP-CLI.",
                "test_binding": "tests/test_beta.py::test_beta",
            },
            {
                "id": id_edited,
                "rule": "Invariant whose test directly imports edited file src/app/cli/main.py.",
                "test_binding": "tests/test_alpha.py::test_alpha",
            },
            {
                "id": id_tbd_coupled,
                "rule": "Invariant with omitted test_binding resolved via AST matching_tests importing sibling helpers.py.",
                "test_binding": "",
            },
        ],
    }
    cap_app.write_text(dump_spec_document(d_cap.frontmatter, d_cap.body), encoding="utf-8")

    (repo / "src" / "app" / "cli").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "app" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "app" / "cli" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "app" / "cli" / "main.py").write_text("def run() -> int:\n    return 0\n", encoding="utf-8")
    (repo / "src" / "app" / "cli" / "helpers.py").write_text(
        "def fmt(v: str) -> str:\n    return v\n", encoding="utf-8"
    )

    (repo / "tests").mkdir(parents=True, exist_ok=True)
    (repo / "tests" / "test_alpha.py").write_text(
        f"from app.cli.main import run\n\ndef test_alpha():\n    # {id_edited}\n    assert run() == 0\n",
        encoding="utf-8",
    )
    (repo / "tests" / "test_beta.py").write_text(
        f"from app.cli.helpers import fmt\n\ndef test_beta():\n    # {id_sibling}\n    # {id_tbd_coupled}\n    assert fmt('x') == 'x'\n",
        encoding="utf-8",
    )
    (repo / "tests" / "test_subproc_narrow.py").write_text(
        f"def test_narrow():\n    # {id_narrow}\n    assert True\n",
        encoding="utf-8",
    )
    (repo / "tests" / "test_subproc_broad.py").write_text(
        f"def test_broad():\n    # {id_broad}\n    assert True\n",
        encoding="utf-8",
    )
    (repo / "tests" / "test_unrelated.py").write_text(
        f"def test_explicit():\n    # {id_explicit}\n    assert True\n\n"
        f"def test_foundational():\n    # {id_foundational}\n    assert True\n\n"
        f"def test_cli_incidental_noise():\n    # {id_incidental}\n    assert True\n",
        encoding="utf-8",
    )

    idx = ArchitecturalIndex(eidos_dir=eidos_dir, repo_root=repo)
    try:
        idx.sync(auto_semantic=False)
        conn = idx._get_connection()

        # Test imports persisted in code_symbols (is_test = 1) with ZERO leakage into production code_couplings
        test_imp_cnt = conn.execute(
            "SELECT COUNT(*) FROM code_symbols WHERE kind = 'import' AND is_test = 1;"
        ).fetchone()[0]
        assert test_imp_cnt >= 2
        polluted_couplings = conn.execute(
            "SELECT COUNT(*) FROM code_couplings WHERE src_file LIKE 'tests/%';"
        ).fetchone()[0]
        assert polluted_couplings == 0
        assert conn.execute("SELECT value FROM metadata WHERE key = 'test_imports_indexed';").fetchone()[0] == "1"

        # Deep slice exercises full deterministic precedence across all 8 invariants (including TBD matching_tests coupling)
        deep_sl = idx.get_tiered_context_slice(files=["src/app/cli/main.py"], tier="deep")
        deep_inv_ids = [i["id"] for i in deep_sl.invariants]
        assert deep_inv_ids[:8] == [
            id_explicit,
            id_edited,
            id_sibling,
            id_tbd_coupled,
            id_narrow,
            id_broad,
            id_foundational,
            id_incidental,
        ], f"Unexpected hermetic invariant order: {deep_inv_ids}"

        # Guardrails slice honors token budget and retains top causally coupled invariants
        guard_sl = idx.get_tiered_context_slice(files=["src/app/cli/main.py"], tier="guardrails")
        assert guard_sl.token_estimate <= TIER_TOKEN_BUDGETS["guardrails"]
        assert [i["id"] for i in guard_sl.invariants[:4]] == [
            id_explicit,
            id_edited,
            id_sibling,
            id_tbd_coupled,
        ]

        # Simulate pre-existing SCHEMA_VERSION 2.22.0 cache missing test_imports_indexed -> one-time backfill
        conn.execute("DELETE FROM metadata WHERE key = 'test_imports_indexed';")
        conn.execute("DELETE FROM code_symbols WHERE kind = 'import' AND is_test = 1;")
        conn.commit()
        dirty_before, _, _ = idx.is_dirty()
        assert dirty_before is True
        idx.sync(auto_semantic=False)
        assert (
            conn.execute("SELECT COUNT(*) FROM code_symbols WHERE kind = 'import' AND is_test = 1;").fetchone()[0] >= 2
        )
        assert conn.execute("SELECT value FROM metadata WHERE key = 'test_imports_indexed';").fetchone()[0] == "1"
        dirty_after, _, _ = idx.is_dirty()
        assert dirty_after is False
    finally:
        idx.close()

    # 3. Direct unit check of shared resolve_import_target_file fallback_parent & relative JS/TS resolution
    known = {"src/pkg/mod.py", "src/pkg/__init__.py", "web/ui/button.tsx", "web/ui/index.ts"}
    mod_map = build_module_to_file_map(known)
    assert (
        resolve_import_target_file("tests/t.py", "pkg.mod.Symbol", known, mod_map, fallback_parent=True)
        == "src/pkg/mod.py"
    )
    assert resolve_import_target_file("tests/t.py", "pkg.mod.Symbol", known, mod_map, fallback_parent=False) is None
    assert resolve_import_target_file("web/ui/page.test.ts", "./button.js", known, mod_map) == "web/ui/button.tsx"
    assert (
        resolve_import_target_file("web/ui/page.test.ts", "web/ui/index/Export", known, mod_map, fallback_parent=True)
        == "web/ui/index.ts"
    )
    assert resolve_import_target_file("", "pkg.mod", known, mod_map) is None

    # 4. Synthetic guardrails headroom check: secondary 1-hop components and secondary DEC-* prose trim
    #    before evicting 1-hop capability invariants below min_len=3
    syn = TieredContextSlice(
        query={"files": ["src/app/main.py"]},
        tier="guardrails",
        components=[
            {
                "id": "CMP-PRIMARY",
                "title": "Primary Component",
                "type": "tech/component",
                "container": "CNT-PRIMARY",
                "source_path": ["src/app/main.py"],
                "file_path": "eidos/architecture/components/primary.adoc",
            },
            {
                "id": "CNT-PRIMARY",
                "title": "Primary Container",
                "type": "tech/container",
                "runtime": "Python 3.12",
                "source_path": "src/app",
                "file_path": "eidos/architecture/containers/primary.adoc",
                "decisions": [
                    {
                        "id": "DEC-PRIM-1",
                        "title": "Primary Architecture Decision",
                        "decision": "Keep primary contract strict. " * 6,
                        "rationale": "Avoids breaking downstream callers. " * 6,
                    }
                ],
            },
            *[
                {
                    "id": f"CNT-DEP-{i}",
                    "title": f"Secondary Dependency Container {i}",
                    "type": "tech/container",
                    "runtime": "Python 3.12 / FastAPI / SQLite",
                    "source_path": f"src/dep_{i}",
                    "file_path": f"eidos/architecture/containers/dep_{i}.adoc",
                }
                for i in range(4)
            ],
        ],
        capabilities=[
            {
                "id": "CAP-CORE-001",
                "title": "Core Capability",
                "domain": "DOM-CORE",
                "file_path": "eidos/domains/core/capabilities/cap-core-001.adoc",
            }
        ],
        invariants=[
            {
                "id": f"RULE-CORE-{i:02d}",
                "rule": f"Core invariant {i} must remain enforced across every boundary transaction.",
                "has_test_coverage": True,
                "test_binding": f"tests/test_core.py::test_core_{i}",
            }
            for i in range(1, 10)
        ],
        policies=[
            {
                "id": "POL-001",
                "title": "Core Policy",
                "category": "governance",
                "severity": "blocking",
                "file_path": "eidos/policies/pol-001.adoc",
            }
        ],
        matched_reasons={
            "CMP-PRIMARY": ["Source file match on 'src/app/main.py'"],
            "CNT-PRIMARY": ["Owning container of CMP-PRIMARY"],
            **{f"CNT-DEP-{i}": ["Internal dependency"] for i in range(4)},
        },
    )
    syn._enforce_token_budget(primary_cids={"CMP-PRIMARY", "CNT-PRIMARY"})
    assert syn.token_estimate <= TIER_TOKEN_BUDGETS["guardrails"]
    assert len(syn.to_json()) // 4 <= TIER_TOKEN_BUDGETS["guardrails"]
    assert len(syn.invariants) >= 3, (
        f"Expected >= 3 invariants retained after trimming secondary deps, got {len(syn.invariants)}"
    )
    assert [i["id"] for i in syn.invariants[:3]] == ["RULE-CORE-01", "RULE-CORE-02", "RULE-CORE-03"]
    assert {"CMP-PRIMARY", "CNT-PRIMARY"} <= {c["id"] for c in syn.components}


def test_context_secondary_team_memory_attachment_and_budget_precedence(tmp_path) -> None:
    """Verify INV-CONTEXT-TEAM-MEMORY-ATTACHMENT: secondary budgeted MEM-* attachment across anchor/guardrails/deep, FTS non-eviction, global caps, honest empty slices, and Stage 0 memory-first pruning."""
    import subprocess
    from pathlib import Path

    from eidos.core.index.context_memories import extract_memory_sections
    from eidos.core.index.context_models import TIER_TOKEN_BUDGETS, TieredContextSlice
    from eidos.core.index.graph_index import ArchitecturalIndex
    from eidos.core.lifecycle.initializer import init_repository

    # Direct extract_memory_sections checks
    assert extract_memory_sections("") == {}
    assert extract_memory_sections("Just plain prose without headings.") == {}
    sec = extract_memory_sections(
        "---\nid: MEM-X\n---\n= Title\n\n== Guidance\nRun via uv.\n\n== Rationale\nLocked deps.\n"
    )
    assert sec == {"guidance": "Run via uv.", "why": "Locked deps."}

    def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)

    repo = tmp_path / "ctx_mem_repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@eidos.dev")
    _git(repo, "config", "user.name", "Test")

    init_repository(repo, title="Payments Repo")
    eidos_dir = repo / "eidos"
    (eidos_dir / "domains" / "payments" / "capabilities").mkdir(parents=True, exist_ok=True)
    (eidos_dir / "architecture" / "containers").mkdir(parents=True, exist_ok=True)
    (eidos_dir / "architecture" / "components").mkdir(parents=True, exist_ok=True)
    (eidos_dir / "policies").mkdir(parents=True, exist_ok=True)
    (eidos_dir / "memory" / "archive").mkdir(parents=True, exist_ok=True)

    (eidos_dir / "domains" / "payments" / "domain.adoc").write_text(
        "---\ntype: product/domain\nid: DOM-PAY\ntitle: Payments\nstatus: active\n---\n= Payments\n",
        encoding="utf-8",
    )
    (eidos_dir / "policies" / "pol-pay.adoc").write_text(
        "---\ntype: product/policy\nid: POL-PAY-001\ntitle: Ledger Integrity\nstatus: active\ncategory: reliability\nseverity: blocking\n---\n= Ledger Integrity\n",
        encoding="utf-8",
    )
    (eidos_dir / "architecture" / "containers" / "cnt-pay.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-PAY\n"
        "title: Payments Engine\n"
        "status: active\n"
        "domain: DOM-PAY\n"
        "container_type: service\n"
        "runtime: Python 3.12\n"
        "source_path: src/pay\n"
        "---\n= Payments Engine\n",
        encoding="utf-8",
    )
    (eidos_dir / "architecture" / "components" / "cmp-ledger.adoc").write_text(
        "---\ntype: tech/component\nid: CMP-PAY-LEDGER\ntitle: LedgerWriter\ncontainer: CNT-PAY\nstatus: active\nsource_path: src/pay/ledger.py\n---\n= LedgerWriter\n",
        encoding="utf-8",
    )
    (eidos_dir / "architecture" / "components" / "cmp-webhook.adoc").write_text(
        "---\ntype: tech/component\nid: CMP-PAY-WEBHOOK\ntitle: WebhookProcessor\ncontainer: CNT-PAY\nstatus: active\nsource_path: src/pay/webhook.py\n---\n= WebhookProcessor\n",
        encoding="utf-8",
    )
    (eidos_dir / "domains" / "payments" / "capabilities" / "cap-pay.adoc").write_text(
        "---\n"
        "type: product/capability\n"
        "id: CAP-PAY-001\n"
        "title: Payment Idempotency & Webhook Processing\n"
        "domain: DOM-PAY\n"
        "status: active\n"
        "business:\n"
        "  policies:\n"
        "    - POL-PAY-001\n"
        "technical:\n"
        "  containers:\n"
        "    - CNT-PAY\n"
        "  components:\n"
        "    - CMP-PAY-LEDGER\n"
        "    - CMP-PAY-WEBHOOK\n"
        "  invariants:\n"
        "    - id: INV-PAY-IDEMPOTENCY\n"  # eidos:fixture
        '      rule: "Webhook idempotency retries must never double-charge."\n'
        "---\n= Payment Idempotency\n",
        encoding="utf-8",
    )

    (repo / "src" / "pay").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "pay" / "ledger.py").write_text("def write_entry():\n    return True\n", encoding="utf-8")
    (repo / "src" / "pay" / "webhook.py").write_text("def handle_webhook():\n    return True\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    head_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    idx = ArchitecturalIndex(db_path=eidos_dir / ".cache" / "index.db", repo_root=repo, eidos_dir=eidos_dir)
    try:
        idx.sync(force=True, auto_semantic=False)

        # 1. Zero memories -> memories key omitted from JSON and section omitted from Markdown (0-token overhead)
        sl_empty_mem = idx.get_tiered_context_slice(files=["src/pay/ledger.py"], tier="guardrails", use_semantic=False)
        assert sl_empty_mem.memories == []
        assert "memories" not in sl_empty_mem.to_dict()
        assert "Team Operational Memories" not in sl_empty_mem.to_markdown()

        # 2. Populate anchored, brace-glob, stale, task-FTS, 6 global (1 expired), and archived memories
        (eidos_dir / "memory" / "mem-ledger-exact-1111.adoc").write_text(
            "---\n"
            "type: agent/memory\n"
            "id: MEM-LEDGER-EXACT-1111\n"
            'title: "Ledger serializable transactions"\n'
            "category: feedback\n"
            "status: active\n"
            'summary: "Always wrap ledger inserts in SERIALIZABLE transactions."\n'
            "targets:\n"
            "  entities: [CMP-PAY-LEDGER]\n"
            "  paths: [src/pay/ledger.py]\n"
            'verified_at: "2026-11-24"\n'
            f'verified_commit: "{head_sha}"\n'
            "---\n"
            "= Memory: Ledger serializable transactions\n\n"
            "== Guidance\nOpen an explicit SERIALIZABLE block before mutating ledger rows.\n\n"
            "== Why\nConcurrent retries previously caused phantom balance drift.\n",
            encoding="utf-8",
        )
        (eidos_dir / "memory" / "mem-glob-brace-2222.adoc").write_text(
            "---\n"
            "type: agent/memory\n"
            "id: MEM-GLOB-BRACE-2222\n"
            'title: "Payments module uses decimal math"\n'
            "category: feedback\n"
            "status: active\n"
            'summary: "Never use float arithmetic in ledger or webhook modules."\n'
            "targets:\n"
            '  paths: ["src/pay/{ledger,webhook}.py"]\n'
            'verified_at: "2026-11-24"\n'
            f'verified_commit: "{head_sha}"\n'
            "---\n= Decimal Math\n",
            encoding="utf-8",
        )
        (eidos_dir / "memory" / "mem-stale-path-3333.adoc").write_text(
            "---\n"
            "type: agent/memory\n"
            "id: MEM-STALE-PATH-3333\n"
            'title: "Legacy CSV reconciliation script"\n'
            "category: project\n"
            "status: active\n"
            'summary: "Legacy reconciliation hook runs nightly."\n'
            "targets:\n"
            "  entities: [CMP-PAY-LEDGER]\n"
            "  paths: [src/pay/deleted_legacy.py]\n"
            'verified_at: "2026-10-10"\n'
            f'verified_commit: "{head_sha}"\n'
            "---\n= Legacy CSV\n",
            encoding="utf-8",
        )
        (eidos_dir / "memory" / "mem-task-only-4444.adoc").write_text(
            "---\n"
            "type: agent/memory\n"
            "id: MEM-TASK-ONLY-4444\n"
            'title: "Deadletter exponential backoff"\n'
            "category: reference\n"
            "status: active\n"
            'summary: "Webhook deadletter retries use jittered exponential backoff."\n'
            "targets:\n"
            "  entities: [CMP-PAY-WEBHOOK]\n"
            'verified_at: "2026-11-24"\n'
            "---\n= Deadletter Backoff\n",
            encoding="utf-8",
        )
        # 6 untargeted globals (1 expired so fresh globals win the cap)
        for i in range(1, 6):
            (eidos_dir / "memory" / f"mem-global-fresh-{i:02d}aa.adoc").write_text(
                "---\n"
                "type: agent/memory\n"
                f"id: MEM-GLOBAL-FRESH-{i:02d}AA\n"
                f'title: "Global rule {i}"\n'
                "category: feedback\n"
                "status: active\n"
                f'summary: "Global team convention #{i}."\n'
                f'verified_at: "2026-11-{10 + i:02d}"\n'
                "---\n= Global\n",
                encoding="utf-8",
            )
        (eidos_dir / "memory" / "mem-global-expired-06aa.adoc").write_text(
            "---\n"
            "type: agent/memory\n"
            "id: MEM-GLOBAL-EXPIRED-06AA\n"
            'title: "Expired freeze"\n'
            "category: project\n"
            "status: active\n"
            'summary: "Temporary release freeze."\n'
            'verified_at: "2025-01-01"\n'
            'expires_at: "2025-02-01"\n'
            "---\n= Expired\n",
            encoding="utf-8",
        )
        # Archived-path memory (even with status: active) must never attach
        (eidos_dir / "memory" / "archive" / "mem-arch-active-9999.adoc").write_text(
            "---\n"
            "type: agent/memory\n"
            "id: MEM-ARCH-ACTIVE-9999\n"
            'title: "Archived stray"\n'
            "category: feedback\n"
            "status: active\n"
            'summary: "Must not attach from archive directory."\n'
            "targets:\n"
            "  entities: [CMP-PAY-LEDGER]\n"
            'verified_at: "2026-11-24"\n'
            "---\n= Archived Stray\n",
            encoding="utf-8",
        )

        idx.sync(force=False, auto_semantic=False)

        # 3. Anchor tier: <=2 anchored, 0 globals, minimal JSON keys, within 300-token budget
        sl_anchor = idx.get_tiered_context_slice(files=["src/pay/ledger.py"], tier="anchor", use_semantic=False)
        d_anchor = sl_anchor.to_dict()
        assert sl_anchor.token_estimate <= TIER_TOKEN_BUDGETS["anchor"]
        assert 1 <= len(d_anchor.get("memories", [])) <= 2
        assert all(not m["id"].startswith("MEM-GLOBAL-") for m in d_anchor["memories"])
        assert all(m["id"] != "MEM-ARCH-ACTIVE-9999" for m in d_anchor["memories"])
        assert set(d_anchor["memories"][0].keys()) == {"id", "category", "summary", "freshness"}

        # 4. Guardrails tier: <=3 anchored + <=2 fresh globals (expired excluded), stale reason included
        sl_guard = idx.get_tiered_context_slice(files=["src/pay/ledger.py"], tier="guardrails", use_semantic=False)
        d_guard = sl_guard.to_dict()
        md_guard = sl_guard.to_markdown()
        assert sl_guard.token_estimate <= TIER_TOKEN_BUDGETS["guardrails"]
        guard_mems = d_guard["memories"]
        guard_ids = [m["id"] for m in guard_mems]
        assert "MEM-LEDGER-EXACT-1111" in guard_ids
        assert "MEM-GLOB-BRACE-2222" in guard_ids
        assert "MEM-STALE-PATH-3333" in guard_ids
        assert "MEM-ARCH-ACTIVE-9999" not in guard_ids
        assert "MEM-GLOBAL-EXPIRED-06AA" not in guard_ids
        global_in_guard = [m for m in guard_mems if m["id"].startswith("MEM-GLOBAL-")]
        assert len(global_in_guard) == 2
        stale_entry = next(m for m in guard_mems if m["id"] == "MEM-STALE-PATH-3333")
        assert stale_entry["freshness"] == "stale"
        assert "missing target path" in stale_entry["freshness_reason"]
        assert "Team Operational Memories" in md_guard
        assert "*Freshness:*" in md_guard

        # 5. Deep tier: <=5 anchored + <=3 globals, includes extracted guidance & why sections
        sl_deep = idx.get_tiered_context_slice(files=["src/pay/ledger.py"], tier="deep", use_semantic=False)
        d_deep = sl_deep.to_dict()
        md_deep = sl_deep.to_markdown()
        assert sl_deep.token_estimate <= TIER_TOKEN_BUDGETS["deep"]
        deep_mems = d_deep["memories"]
        assert len([m for m in deep_mems if m["id"].startswith("MEM-GLOBAL-")]) == 3
        exact_deep = next(m for m in deep_mems if m["id"] == "MEM-LEDGER-EXACT-1111")
        assert "SERIALIZABLE" in exact_deep["guidance"]
        assert "phantom balance drift" in exact_deep["why"]
        assert "*Guidance:*" in md_deep and "*Why:*" in md_deep

        # 6. Component query (-c) and Task query (-t) secondary FTS without displacing primary entities
        sl_comp = idx.get_tiered_context_slice(component="CMP-PAY-LEDGER", tier="guardrails", use_semantic=False)
        assert "MEM-LEDGER-EXACT-1111" in [m["id"] for m in sl_comp.memories]

        sl_task = idx.get_tiered_context_slice(
            task="webhook idempotency deadletter exponential backoff",
            tier="guardrails",
            use_semantic=False,
        )
        assert any(c["id"] in ("CNT-PAY", "CMP-PAY-WEBHOOK") for c in sl_task.components)
        assert any(i["id"] == "INV-PAY-IDEMPOTENCY" for i in sl_task.invariants)  # eidos:fixture
        assert "MEM-TASK-ONLY-4444" in [m["id"] for m in sl_task.memories]

        # 7. Honest Empty Slice: unmatched explicit query returns zero globals & omits memories key
        sl_none = idx.get_tiered_context_slice(
            task="quantum_nonexistent_xyzzy_99999",
            tier="guardrails",
            use_semantic=False,
        )
        assert sl_none.components == []
        assert sl_none.memories == []
        assert "memories" not in sl_none.to_dict()
    finally:
        idx.close()

    # 8. Stage 0 memory-first pruning precedence (guardrails & anchor):
    #    Memories yield (globals -> compact -> anchored tail) BEFORE any architectural invariant is evicted.
    heavy_invs = [
        {
            "id": f"RULE-PAY-{i:02d}",
            "rule": f"Critical payment ledger invariant {i} enforcing idempotent settlement semantics.",
            "has_test_coverage": True,
            "test_binding": f"tests/test_pay.py::test_rule_{i}",
        }
        for i in range(1, 6)
    ]
    base_kwargs = dict(
        query={"files": ["src/pay/ledger.py"]},
        components=[
            {
                "id": "CMP-PAY-LEDGER",
                "title": "LedgerWriter",
                "type": "tech/component",
                "container": "CNT-PAY",
                "source_path": ["src/pay/ledger.py"],
                "file_path": "eidos/architecture/components/cmp-ledger.adoc",
            },
            {
                "id": "CNT-PAY",
                "title": "Payments Engine",
                "type": "tech/container",
                "runtime": "Python 3.12",
                "source_path": "src/pay",
                "file_path": "eidos/architecture/containers/cnt-pay.adoc",
                "decisions": [
                    {
                        "id": "DEC-PAY-SERIAL",
                        "title": "Serializable Ledger Isolation",
                        "decision": "Enforce serializable transactions for every balance mutation. " * 3,
                        "rationale": "Prevents double-spend anomalies under concurrent webhooks. " * 3,
                    }
                ],
            },
        ],
        capabilities=[
            {
                "id": "CAP-PAY-001",
                "title": "Payment Idempotency",
                "domain": "DOM-PAY",
                "file_path": "eidos/domains/payments/capabilities/cap-pay.adoc",
            }
        ],
        invariants=heavy_invs,
        policies=[
            {
                "id": "POL-PAY-001",
                "title": "Ledger Integrity",
                "category": "reliability",
                "severity": "blocking",
                "file_path": "eidos/policies/pol-pay.adoc",
            }
        ],
        matched_reasons={"CMP-PAY-LEDGER": ["Source file match"]},
    )
    baseline_no_mem = TieredContextSlice(
        tier="guardrails", **{k: list(v) if isinstance(v, list) else dict(v) for k, v in base_kwargs.items()}
    )
    baseline_no_mem._enforce_token_budget(primary_cids={"CMP-PAY-LEDGER", "CNT-PAY"})
    baseline_inv_ids = [i["id"] for i in baseline_no_mem.invariants]

    bloated_memories = [
        {
            "id": f"MEM-ANCHOR-{i}",
            "category": "feedback",
            "summary": ("Anchored memory operational warning detail " * 5).strip(),
            "freshness": "stale",
            "freshness_reason": "8 commits modified src/pay/ledger.py since deadbee",
            "file_path": f"eidos/memory/mem-anchor-{i}.adoc",
            "match_scope": "anchored",
        }
        for i in range(1, 5)
    ] + [
        {
            "id": f"MEM-GLOB-{i}",
            "category": "project",
            "summary": ("Global repo convention explanation prose " * 5).strip(),
            "freshness": "fresh",
            "freshness_reason": "no path anchors",
            "file_path": f"eidos/memory/mem-glob-{i}.adoc",
            "match_scope": "global",
        }
        for i in range(1, 4)
    ]
    with_mems = TieredContextSlice(
        tier="guardrails",
        memories=list(bloated_memories),
        **{k: list(v) if isinstance(v, list) else dict(v) for k, v in base_kwargs.items()},
    )
    with_mems._enforce_token_budget(primary_cids={"CMP-PAY-LEDGER", "CNT-PAY"})
    assert with_mems.token_estimate <= TIER_TOKEN_BUDGETS["guardrails"]
    assert len(with_mems.to_json()) // 4 <= TIER_TOKEN_BUDGETS["guardrails"]
    assert [i["id"] for i in with_mems.invariants] == baseline_inv_ids, (
        "Stage 0 must prune memories before evicting any architectural invariant"
    )
    assert all(m.get("match_scope") != "global" for m in with_mems.memories)

    # Anchor tier Stage 0 pruning drops memories before dropping primary component
    anchor_bloated = TieredContextSlice(
        tier="anchor",
        memories=list(bloated_memories[:3]),
        **{k: list(v) if isinstance(v, list) else dict(v) for k, v in base_kwargs.items()},
    )
    anchor_bloated._enforce_token_budget(primary_cids={"CMP-PAY-LEDGER"})
    assert anchor_bloated.token_estimate <= TIER_TOKEN_BUDGETS["anchor"]
    assert len(anchor_bloated.to_json()) // 4 <= TIER_TOKEN_BUDGETS["anchor"]
    assert any(c["id"] == "CMP-PAY-LEDGER" for c in anchor_bloated.components)
    assert len(anchor_bloated.memories) < 3


def test_context_structural_tie_breaking_for_capabilities_and_policies(tmp_path):  # eidos:fixture
    """Verify DLT-2026-175: tied 1-hop capabilities rank by shared architectural links and invariant breadth, and policies rank by severity, top-capability affinity, and reach rather than raw alphabetical ID."""
    from eidos.core.index.context import build_tiered_context_slice
    from eidos.core.index.graph_index import ArchitecturalIndex

    repo = tmp_path / "tie_break_repo"
    eidos_dir = repo / "eidos"
    (eidos_dir / "architecture" / "containers").mkdir(parents=True)
    (eidos_dir / "architecture" / "contracts").mkdir(parents=True)
    (eidos_dir / "domains" / "billing" / "capabilities").mkdir(parents=True)
    (eidos_dir / "policies").mkdir(parents=True)
    (repo / "src" / "cli").mkdir(parents=True)
    (repo / "src" / "core").mkdir(parents=True)
    (repo / "src" / "cli" / "entry.py").write_text("from core.engine import run\n", encoding="utf-8")
    (repo / "src" / "core" / "engine.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (eidos_dir / "domains" / "billing" / "domain.adoc").write_text(
        "---\ntype: product/domain\nid: DOM-BILLING\ntitle: Billing\nstatus: active\nclassification: core\n---\n= Billing\n",
        encoding="utf-8",
    )
    (eidos_dir / "architecture" / "containers" / "cli.adoc").write_text(
        "---\ntype: tech/container\nid: CNT-CLI\ntitle: CLI\nstatus: active\ncontainer_type: cli_app\n"
        "runtime: Python\nsource_path: src/cli\ndomain: DOM-BILLING\ncontracts:\n  provides:\n    - CTR-CLI\n---\n= CLI\n",
        encoding="utf-8",
    )
    (eidos_dir / "architecture" / "containers" / "core.adoc").write_text(
        "---\ntype: tech/container\nid: CNT-CORE\ntitle: Core\nstatus: active\ncontainer_type: library\n"
        "runtime: Python\nsource_path: src/core\ndomain: DOM-BILLING\n---\n= Core\n",
        encoding="utf-8",
    )
    (eidos_dir / "architecture" / "contracts" / "cli.adoc").write_text(
        "---\ntype: tech/contract\nid: CTR-CLI\ntitle: CLI Contract\nstatus: active\nprotocol: cli_args\ncontainer: CNT-CLI\n---\n= CLI\n",
        encoding="utf-8",
    )
    (eidos_dir / "policies" / "alpha-advisory.adoc").write_text(
        "---\ntype: product/policy\nid: POL-ALPHA-ADVISORY\ntitle: Alpha Advisory\nstatus: active\ncategory: governance\n"
        "severity: advisory\ninvariants:\n  - id: INV-POL-ALPHA\n    rule: Advisory rule.\n---\n= Alpha\n",
        encoding="utf-8",
    )
    (eidos_dir / "policies" / "mid-blocking.adoc").write_text(
        "---\ntype: product/policy\nid: POL-MID-BLOCKING\ntitle: Mid Blocking\nstatus: active\ncategory: governance\n"
        "severity: blocking\ninvariants:\n  - id: INV-POL-MID\n    rule: Mid blocking rule.\n---\n= Mid\n",
        encoding="utf-8",
    )
    (eidos_dir / "policies" / "zeta-blocking.adoc").write_text(
        "---\ntype: product/policy\nid: POL-ZETA-BLOCKING\ntitle: Zeta Blocking\nstatus: active\ncategory: governance\n"
        "severity: blocking\ninvariants:\n  - id: INV-POL-ZETA\n    rule: Zeta blocking rule.\n---\n= Zeta\n",
        encoding="utf-8",
    )
    # Alphabetically first capability has fewer shared links (1) and fewer invariants (1)
    (eidos_dir / "domains" / "billing" / "capabilities" / "alpha-tiny.adoc").write_text(
        "---\ntype: product/capability\nid: CAP-ALPHA-TINY\ntitle: Alpha Tiny\ndomain: DOM-BILLING\nstatus: active\n"
        "business:\n  policies:\n    - eidos/policies/alpha-advisory.adoc\n    - eidos/policies/zeta-blocking.adoc\n"
        "technical:\n  containers:\n    - CNT-CORE\n  invariants:\n"
        "    - id: INV-ALPHA-ONE\n      rule: Tiny rule.\n---\n= Alpha Tiny\n",
        encoding="utf-8",
    )
    # Alphabetically later capability shares both CNT-CORE + CTR-CLI (2 links) and has 2 invariants
    (eidos_dir / "domains" / "billing" / "capabilities" / "zeta-core.adoc").write_text(
        "---\ntype: product/capability\nid: CAP-ZETA-CORE\ntitle: Zeta Core\ndomain: DOM-BILLING\nstatus: active\n"
        "business:\n  policies:\n    - eidos/policies/mid-blocking.adoc\n    - eidos/policies/zeta-blocking.adoc\n"
        "technical:\n  containers:\n    - CNT-CORE\n  contracts:\n    - CTR-CLI\n  invariants:\n"
        "    - id: INV-ZETA-ONE\n      rule: Core rule one.\n"
        "    - id: INV-ZETA-TWO\n      rule: Core rule two.\n---\n= Zeta Core\n",
        encoding="utf-8",
    )

    idx = ArchitecturalIndex(repo_root=repo, eidos_dir=eidos_dir)
    idx.sync(force=True, auto_semantic=False)
    sl = build_tiered_context_slice(idx, files=["src/cli/entry.py"], tier="deep", use_semantic=False)
    assert [c["id"] for c in sl.capabilities[:2]] == ["CAP-ZETA-CORE", "CAP-ALPHA-TINY"]
    assert [p["id"] for p in sl.policies] == ["POL-ZETA-BLOCKING", "POL-MID-BLOCKING", "POL-ALPHA-ADVISORY"]
