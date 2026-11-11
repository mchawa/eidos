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

    # Bound capabilities and invariants
    cap_ids = {c["id"] for c in res.capabilities}
    assert "CAP-PROD-001" in cap_ids
    inv_ids = {i["id"] for i in res.invariants}
    assert "INV-SPEC-VALIDATION" in inv_ids

    # Governing policies
    policy_ids = {p["id"] for p in res.policies}
    assert "POL-001-GIT-SOURCE-OF-TRUTH" in policy_ids

    # DLT-2026-125: guardrails/deep project domain_path, design, capability personas/policies/experience, and policy file_path
    proj = res.to_dict()
    cli_comp = proj["components"][0]
    assert cli_comp.get("domain_path", "").endswith("domain.adoc")
    cap_prod = next(c for c in proj["capabilities"] if c["id"] == "CAP-PROD-001")
    assert cap_prod.get("file_path", "").endswith(".adoc")
    assert cap_prod.get("personas")
    pol_entry = next(p for p in proj["policies"] if p["id"] == "POL-001-GIT-SOURCE-OF-TRUTH")
    assert pol_entry.get("file_path", "").endswith("POL-001-git-as-single-source-of-truth.adoc")
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
    assert any(c["id"] == "CAP-EXP-001" for c in dom_file_sl.capabilities)

    anchor_portal = get_context_slice(files=["src/eidos/portal/builder.py"], tier="anchor").to_dict()
    assert "domain_path" not in anchor_portal["components"][0]
    assert "design" not in anchor_portal["components"][0]

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
    assert "CAP-VCS-001" in cap_ids

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
    assert "CAP-VCS-001" in res_task.output


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

    # 2. sync
    res_sync = runner.invoke(app, ["index", "sync"])
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
    assert any(c["id"] == "CAP-AGENT-001" for c in ranked_sl.capabilities)

    repo_map_sl = get_context_slice(
        task="token compression for repository structural map",
        tier="guardrails",
    )
    assert repo_map_sl.token_estimate <= TIER_TOKEN_BUDGETS["guardrails"]
    assert [i["id"] for i in repo_map_sl.invariants[:2]] == [
        "INV-INDEX-AI-REPO-MAP-BUDGET",
        "INV-CONTEXT-TOKEN-COMPRESSION",
    ]
    assert any(c["id"] == "CAP-AGENT-002" for c in repo_map_sl.capabilities)

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
        assert plugin_sl.token_estimate <= max_budget, f"{tier} token_estimate={plugin_sl.token_estimate} > {max_budget}"
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
    inline component (CAP-AGENT-002 -> CMP-AST-SYMBOL-INDEXER), and stays within anchor/guardrails/deep budgets
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
            assert "CAP-AGENT-002" in cap_ids
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

    assert len(ENTITY_PREFIX_REGISTRY) == 18
    assert len(TYPE_TO_SCHEMA_NAME) == 16
    assert DOC_TYPE_TO_PREFIX["tech/component"] == "CMP-"
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
