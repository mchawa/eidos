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
    """Verify fallback behavior when empty or unmatched query is provided."""
    # Unmatched query
    res_unmatched = get_context_slice(component="CMP-NONEXISTENT")
    assert len(res_unmatched.components) >= 1
    assert any(c["id"] in ["CNT-EIDOS-CLI", "CNT-EIDOS-CORE"] for c in res_unmatched.components)

    # Empty query (all components overview)
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
    # CLI should not be matched via "cli" in "client"
    assert "CNT-EIDOS-CLI" not in matched_ids or "Fallback baseline component anchor" in res.matched_reasons.get("CNT-EIDOS-CLI", [])
    if "CNT-EIDOS-CLI" in res.matched_reasons:
        assert not any("matches slug 'cli'" in r for r in res.matched_reasons["CNT-EIDOS-CLI"])


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





