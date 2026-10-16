"""Tests verifying architectural subgraph context slicing (Initiative G)."""

import json

from typer.testing import CliRunner

from eidos.cli.main import app
from eidos.core.workspace.context import (
    ArchitecturalContextSlice,
    get_context_slice,
)

runner = CliRunner()


def test_context_slice_by_file():
    """Verify INV-AGENT-CONTEXT-SLICING: Slicing by file path extracts owning component, dependencies, contracts, and invariants."""
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
    data = json.loads(js_str)
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

    # 2. JSON output with -c and --format json
    res_json = runner.invoke(app, ["context", "-c", "CNT-EIDOS-CLI", "--format", "json"])
    assert res_json.exit_code == 0
    data = json.loads(res_json.output)
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
        "name: AuthService\n"
        "container: CNT-AUTH\n"
        "status: active\n"
        "source_path: src/auth\n"
        "responsibility: Auth service\n"
        "---\n"
        "= Auth\n"
    )

    (pols_dir / "security.adoc").write_text(
        "---\n"
        "type: product/policy\n"
        "id: POL-SEC-001\n"
        "title: Security Policy\n"
        "category: security\n"
        "rule: Never log passwords\n"
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
        "rule: Retain data for 90 days\n"
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
    blast_data = json.loads(res_blast_json.output)
    assert blast_data["entity_id"] == "CNT-EIDOS-CORE"


def test_context_slice_includes_decisions_and_tiered_markdown():
    """Verify INV-AGENT-CONTEXT-SLICING: eidos context includes container decisions and rejected_alternatives in JSON and formats Markdown by tier."""
    # 1. CLI JSON output for CNT-EIDOS-INDEX
    res_json = runner.invoke(app, ["context", "--component", "CNT-EIDOS-INDEX", "--format", "json"])
    assert res_json.exit_code == 0
    payload = json.loads(res_json.output)
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
    assert "quality_attributes" not in anchor_comp
    assert "schema" not in anchor_comp
    if anchor_comp.get("decisions"):
        assert "rationale" not in anchor_comp["decisions"][0]

    guard_comp = guard_dict["components"][0]
    assert "quality_attributes" not in guard_comp
    if guard_comp.get("decisions"):
        assert "rationale" in guard_comp["decisions"][0]

    deep_comp = deep_dict["components"][0]
    assert "quality_attributes" in deep_comp and "components" in deep_comp

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
        task="hooks should block the stop event only once per failure",
        tier="guardrails",
    )
    assert ranked_sl.token_estimate <= TIER_TOKEN_BUDGETS["guardrails"]
    assert ranked_sl.invariants, "Expected direct invariant matches to survive guardrails token budget"
    assert ranked_sl.invariants[0]["id"] == "INV-HOOK-BLOCK-ONCE"
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

    # 5. Progressive prose compression compresses verbose descriptions/responsibilities before evicting direct invariants
    from eidos.core.index.graph_index import TieredContextSlice

    synthetic_sl = TieredContextSlice(
        query={"task": "synthetic"},
        tier="guardrails",
        components=[
            {
                "id": "CNT-PRIMARY",
                "name": "Primary",
                "responsibility": "Sentence one of primary responsibility. " + ("Extra prose detail for primary. " * 45),
                "description": "Sentence one of verbose description. " + ("Extra prose detail for primary. " * 45),
                "decisions": [
                    {
                        "id": "DEC-SYN-1",
                        "title": "Synthetic Decision",
                        "decision": "Use progressive field compression. " + ("Detailed decision clause. " * 20),
                        "rationale": "Preserves invariants under token budget pressure. " + ("Detailed rationale clause. " * 20),
                    }
                ],
            },
            {
                "id": "CNT-SECONDARY",
                "name": "Secondary",
                "responsibility": "First sentence of secondary responsibility. " + ("Verbose tail sentence. " * 45),
            },
        ],
        invariants=[
            {"id": f"RULE-SYN-{i}", "rule": f"Rule {i} must hold under budget pressure." * 4}
            for i in range(3)
        ],
    )
    synthetic_sl._enforce_token_budget(
        primary_cids={"CNT-PRIMARY", "CNT-SECONDARY"},
        direct_inv_ids={"RULE-SYN-0", "RULE-SYN-1", "RULE-SYN-2"},
    )
    assert synthetic_sl._compact_prose is True
    assert len(synthetic_sl.components[0]["description"]) <= 120
    assert len(synthetic_sl.components[1]["responsibility"]) <= 120
    assert len(synthetic_sl.invariants) == 3
    assert synthetic_sl.token_estimate <= TIER_TOKEN_BUDGETS["guardrails"]


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
                "components": [{"id": f"CMP-SUB-{i}", "name": f"Sub {i}", "responsibility": "Sub " * 25} for i in range(6)],
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
        invariants=[
            {"id": f"RULE-DIRECT-{i}", "rule": f"Direct invariant rule {i} " * 15} for i in range(15)
        ] + [
            {"id": f"RULE-INDIRECT-{i}", "rule": f"Indirect invariant rule {i} " * 15} for i in range(10)
        ],
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

    # 3. Direct query on an inline C4 Level 3 component (CMP-CLI-APP) and base Markdown C4 L3 rendering
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
    assert "C4 Level 3 Components" in base_md
    assert "CMP-CLI-APP" in base_md

    # 4. Workspace project.py & assets.py edge cases: _is_inside_plugin_or_assets, load_project, get_tracked_files, globs
    import subprocess

    from eidos.core.workspace.assets import (
        get_agent_plugins_schemas_dir,
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
    assert get_agent_plugins_schemas_dir().is_dir()
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

