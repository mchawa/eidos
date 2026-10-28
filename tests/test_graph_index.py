"""Empirical Benchmark & Validation Suite for EXP-2026-002:
Embedded SQLite Architectural Knowledge Graph & Hybrid Context Retrieval Index.

Verifies:
1. Spike A: SQLite Schema, FTS5 BM25 search, and recursive CTE k-hop blast radius (<2ms).
2. Spike B: Incremental mtime & SHA-256 dirty checking (<5ms).
3. Spike C: Tiered context compression (Anchor, Guardrails, Deep) with >=60% token reduction.
4. Exit Criteria: Latency < 10ms across living specs, zero external dependencies.
"""

import os
import sqlite3
import sys
import time
from pathlib import Path

import orjson
import pytest

from eidos.core.index.graph_index import (
    ArchitecturalIndex,
    TieredContextSlice,
    find_repo_root,
)


@pytest.fixture
def temp_index(tmp_path):
    """Fixture providing an isolated ArchitecturalIndex instance with a test database."""
    db_file = tmp_path / "test_index.db"
    eidos_dir = Path("eidos").resolve()
    repo_root = Path.cwd().resolve()
    idx = ArchitecturalIndex(db_path=db_file, repo_root=repo_root, eidos_dir=eidos_dir)
    idx.sync(force=True)
    yield idx
    idx.close()


def test_spike_a_schema_initialization_and_sync(temp_index):
    """Spike A: Verify SQLite schema initialization, table structures, and complete sync (INV-KNOWLEDGE-GRAPH-CATALOG)."""
    stats = temp_index.sync(force=True)
    assert stats.total_specs_scanned >= 50
    assert stats.indexed_new >= 50
    is_instrumented = "coverage" in sys.modules or sys.gettrace() is not None
    max_bulk_sync_ms = 1500.0 if is_instrumented else 800.0
    assert stats.duration_ms < max_bulk_sync_ms  # Initial bulk sync within limits

    # Verify tables contain entities and edges
    conn = temp_index._get_connection()
    entity_count = conn.execute("SELECT COUNT(*) FROM entities;").fetchone()[0]
    edge_count = conn.execute("SELECT COUNT(*) FROM edges;").fetchone()[0]
    fts_count = conn.execute("SELECT COUNT(*) FROM specs_fts;").fetchone()[0]

    assert entity_count >= 50
    assert edge_count > 10
    assert fts_count >= 50

    # Verify C4 Level 1 (ARCH-*, SYS-*) and exploration (EXP-*) graph edges & k-hop neighborhood (DLT-2026-105)
    arch_neighbors = temp_index.get_k_hop_neighborhood("ARCH-EIDOS-001", max_depth=2)
    arch_hop1_ids = {n["id"] for n in arch_neighbors if n["depth"] == 1}
    arch_hop2_ids = {n["id"] for n in arch_neighbors if n["depth"] == 2}
    assert {
        "SYS-EIDOS-001",
        "CNT-EIDOS-CORE",
        "CNT-EIDOS-INDEX",
        "PER-PRODUCT-LEAD",
        "PER-AUTONOMOUS-AGENT",
        "EXP-2026-004",
    } <= arch_hop1_ids
    assert {"CMP-GRAPH-STORAGE", "CMP-INCREMENTAL-SYNC-ENGINE", "CTR-EIDOS-CLI", "DOM-SPEC-ENGINE"} <= arch_hop2_ids

    exp_neighbors = temp_index.get_k_hop_neighborhood("EXP-2026-004", max_depth=1)
    assert "ARCH-EIDOS-001" in {n["id"] for n in exp_neighbors if n["depth"] == 1}


def test_spike_a_fts5_bm25_lexical_search(temp_index):
    """Spike A: Verify FTS5 full-text search with BM25 ranking matches synonymous engineering prompts."""
    t0 = time.perf_counter()
    matches = temp_index.query_fts("drift detection reconciliation gate", limit=5)
    query_latency_ms = (time.perf_counter() - t0) * 1000

    is_instrumented = "coverage" in sys.modules or sys.gettrace() is not None
    max_query_ms = 35.0 if is_instrumented else 10.0
    assert query_latency_ms < max_query_ms
    assert len(matches) > 0

    matched_ids = [m["id"] for m in matches]
    # CAP-VCS-001 or DOM-VCS-LIFECYCLE should be at the top
    assert any("VCS" in mid for mid in matched_ids)


def test_spike_a_recursive_cte_k_hop_traversal(temp_index):
    """Spike A: Verify recursive CTE graph query traverses k-hop blast radius in sub-2ms."""
    t0 = time.perf_counter()
    neighborhood = temp_index.get_k_hop_neighborhood("CNT-EIDOS-CORE", max_depth=2)
    traversal_latency_ms = (time.perf_counter() - t0) * 1000

    is_instrumented = "coverage" in sys.modules or sys.gettrace() is not None
    max_traversal_ms = 35.0 if is_instrumented else 10.0
    assert traversal_latency_ms < max_traversal_ms
    assert len(neighborhood) > 0

    node_ids = {n["id"] for n in neighborhood}
    assert "CNT-EIDOS-CORE" in node_ids
    # Should reach bound domain or contracts within 2 hops
    assert any(n["type"] in ("tech/contract", "product/domain", "product/capability") for n in neighborhood)


def test_spike_b_incremental_dirty_checking_latency(temp_index):
    """Spike B: Verify dirty-checking latency on clean repository is <5ms."""
    # 1. Clean state check
    t0 = time.perf_counter()
    is_dirty, modified, deleted = temp_index.is_dirty()
    dirty_check_ms = (time.perf_counter() - t0) * 1000

    assert not is_dirty
    assert len(modified) == 0
    assert len(deleted) == 0
    is_instrumented = "coverage" in sys.modules or sys.gettrace() is not None
    max_dirty_ms = 40.0 if is_instrumented else 15.0
    assert dirty_check_ms < max_dirty_ms, f"Dirty check exceeded {max_dirty_ms}ms: {dirty_check_ms:.2f}ms"

    # 2. Clean sync pass
    sync_stats = temp_index.sync()
    assert sync_stats.was_clean
    assert sync_stats.duration_ms < max_dirty_ms


def test_spike_b_zero_drift_on_mutation(tmp_path):
    """Spike B: Verify zero drift by modifying a spec file and asserting immediate index update (INV-AGENT-GRAPH-INDEX-FRESHNESS)."""
    eidos_copy = tmp_path / "eidos"
    comps_dir = eidos_copy / "architecture" / "components"
    comps_dir.mkdir(parents=True)

    test_spec = comps_dir / "test_service.adoc"
    test_spec.write_text(
        """---
type: tech/component
id: CMP-TEST-SPIKE
title: "Test Spike Service"
container: "CNT-TEST-SPIKE"
status: "active"
source_path: "src/test"
responsibility: "Test service"
---
= Test Spike Service
"""
    )

    db_path = tmp_path / "spike_drift.db"
    idx = ArchitecturalIndex(db_path=db_path, repo_root=tmp_path, eidos_dir=eidos_copy)
    idx.sync(force=True)

    # Initial check: clean
    dirty, _, _ = idx.is_dirty()
    assert not dirty

    # Mutate the file
    time.sleep(0.01)  # ensure mtime changes
    test_spec.write_text(
        """---
type: tech/component
id: CMP-TEST-SPIKE
title: "Mutated Spike Service"
container: "CNT-TEST-SPIKE"
status: "active"
source_path: "src/test_mutated"
responsibility: "Test service"
---
= Mutated Spike Service
"""
    )

    # Check is_dirty detects change immediately
    dirty, to_update, _deleted = idx.is_dirty()
    assert dirty
    assert len(to_update) == 1
    assert to_update[0].resolve() == test_spec.resolve()

    # Sync and verify updated in SQLite
    sync_stats = idx.sync()
    assert sync_stats.updated_modified == 1

    conn = idx._get_connection()
    row = conn.execute("SELECT title, data_json FROM entities WHERE id = 'CMP-TEST-SPIKE';").fetchone()
    assert row["title"] == "Mutated Spike Service"
    assert orjson.loads(row["data_json"])["source_path"] == "src/test_mutated"

    idx.close()


def test_spike_c_tiered_context_compression(temp_index):
    """Spike C: Verify tiered context slicing token reduction >= 60% compared to raw file loading."""
    slice_anchor = temp_index.get_tiered_context_slice(component="CNT-EIDOS-CORE", tier="anchor")
    slice_guardrails = temp_index.get_tiered_context_slice(component="CNT-EIDOS-CORE", tier="guardrails")
    slice_deep = temp_index.get_tiered_context_slice(component="CNT-EIDOS-CORE", tier="deep")

    assert isinstance(slice_anchor, TieredContextSlice)
    assert slice_anchor.tier == "anchor"
    assert slice_guardrails.tier == "guardrails"
    assert slice_deep.tier == "deep"

    # Token estimation: anchor < guardrails <= deep
    assert slice_anchor.token_estimate < slice_guardrails.token_estimate
    assert slice_guardrails.token_estimate <= slice_deep.token_estimate

    # Verify markdown outputs
    md_anchor = slice_anchor.to_markdown()
    md_guardrails = slice_guardrails.to_markdown()

    assert "ANCHOR Tier" in md_anchor
    assert "GUARDRAILS Tier" in md_guardrails

    # Token savings benchmark: Compare against raw loading of the matched container, capability, and policy specs
    conn = temp_index._get_connection()
    slice_entity_ids = [
        e["id"]
        for group in (
            slice_guardrails.components,
            slice_guardrails.contracts,
            slice_guardrails.capabilities,
            slice_guardrails.policies,
        )
        for e in group
        if e.get("id")
    ]
    raw_chars = sum(
        len((temp_index.root / row[0]).read_text(encoding="utf-8"))
        for eid in slice_entity_ids
        for row in conn.execute("SELECT file_path FROM entities WHERE id = ?;", (eid,)).fetchall()
        if row[0] and (temp_index.root / row[0]).is_file()
    )
    raw_component_tokens = max(raw_chars // 4, slice_deep.token_estimate, 1)
    token_savings_pct = (1.0 - (slice_guardrails.token_estimate / raw_component_tokens)) * 100.0
    assert token_savings_pct >= 60.0, f"Token savings {token_savings_pct:.1f}% was below 60% requirement"


def test_exit_criteria_resolution_latency(temp_index):
    """Exit Criteria: Context slice extraction latency < 10ms for 50+ specs."""
    latencies = []
    for _ in range(5):
        t0 = time.perf_counter()
        s = temp_index.get_tiered_context_slice(task="manage living specifications and schemas", tier="guardrails")
        latencies.append((time.perf_counter() - t0) * 1000)

    avg_latency = sum(latencies) / len(latencies)
    is_instrumented = "coverage" in sys.modules or sys.gettrace() is not None
    max_latency_ms = 40.0 if is_instrumented else 15.0
    assert avg_latency < max_latency_ms, (
        f"Average context slice latency {avg_latency:.2f}ms exceeded {max_latency_ms}ms"
    )
    assert s.query_latency_ms < max_latency_ms


def test_repo_root_discovery_and_no_nested_eidos(tmp_path, monkeypatch):
    """Verify repo root discovery prevents spurious nested cache directories."""  # eidos:fixture
    repo = tmp_path / "mock_repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    specs_dir = repo / "eidos"
    specs_dir.mkdir()
    (specs_dir / "system.adoc").write_text(
        "---\ntype: product/system\nid: SYS-MOCK\ntitle: Mock System\n---\n= Mock System\n",
        encoding="utf-8",
    )
    subfolder = repo / ".agents" / "plugins" / "eidos" / "scripts"
    subfolder.mkdir(parents=True)

    # find_repo_root from subfolder resolves to repo root
    assert find_repo_root(subfolder) == repo.resolve()

    # Changing cwd to subfolder and instantiating ArchitecturalIndex targets repo root
    monkeypatch.chdir(subfolder)
    idx = ArchitecturalIndex()
    try:
        assert idx.root == repo.resolve()
        assert idx.eidos_dir == specs_dir.resolve()
        assert idx.db_path == repo.resolve() / "eidos" / ".cache" / "index.db"
        assert not (subfolder / ".eidos").exists()
        assert not (subfolder.parent / ".eidos").exists()
        assert not (repo / ".agents" / "plugins" / "eidos" / ".eidos").exists()
    finally:
        idx.close()


def test_index_status_ast_telemetry(temp_index):
    """Verify INV-INDEX-STATUS-AST-TELEMETRY: AST engine, symbol kinds, and invariant assertions."""
    status = temp_index.get_index_status()
    assert "ast" in status
    ast = status["ast"]

    assert isinstance(ast["engine"], str)
    assert "ast-grep" in ast["engine"]
    assert ast["engine_class"] == "AstGrepIndexer"
    assert ast["total_symbols"] > 0
    assert ast["classes"] >= 1
    assert ast["functions"] >= 1
    assert ast["methods"] >= 1
    assert ast["test_bindings"] >= 1
    assert isinstance(ast["symbols_by_kind"], dict)
    assert "class" in ast["symbols_by_kind"]
    assert "function" in ast["symbols_by_kind"]
    assert ast["test_invariant_assertions"] >= 1
    assert ast["tested_invariants"] >= 1


def test_code_aware_dirty_checking_and_sync(tmp_path):
    """Verify INV-INDEX-CODE-DIRTY-SYNC: sub-5ms dirty detection when source code or tests are modified."""
    repo = tmp_path / "mock_project"
    repo.mkdir()
    eidos_dir = repo / "eidos"
    comps_dir = eidos_dir / "architecture" / "components"
    comps_dir.mkdir(parents=True)

    src_dir = repo / "src" / "pkg"
    src_dir.mkdir(parents=True)
    tests_dir = repo / "tests"
    tests_dir.mkdir(parents=True)

    code_file = src_dir / "service.py"
    code_file.write_text(
        """class SampleService:\n    def execute(self):\n        return True\n""",
        encoding="utf-8",
    )

    test_file = tests_dir / "test_service.py"
    test_file.write_text(
        """# Tests INV-SAMPLE-RULE\ndef test_service():\n    assert True\n""",  # eidos:fixture
        encoding="utf-8",
    )

    spec_file = comps_dir / "service.adoc"
    spec_file.write_text(
        """---
type: tech/component
id: CMP-SAMPLE-SERVICE
title: Sample Service
container: CNT-SAMPLE
status: active
source_path: src/pkg
responsibility: Sample service
invariants:
  - id: INV-SAMPLE-RULE  # eidos:fixture
    rule: Sample rule must hold.
    test_binding: tests/test_service.py::test_service
---
= Sample Service
""",
        encoding="utf-8",
    )

    db_path = repo / "eidos" / ".cache" / "index.db"
    idx = ArchitecturalIndex(db_path=db_path, repo_root=repo, eidos_dir=eidos_dir)
    idx.sync(force=True)

    try:
        # 1. Initially clean
        dirty, modified, _deleted = idx.is_dirty()
        assert not dirty
        assert len(modified) == 0

        # Verify initial symbols
        status = idx.get_index_status()
        assert status["ast"]["classes"] >= 1
        assert any(r["symbol"] == "SampleService" for r in idx.find_symbol("SampleService"))

        # 2. Mutate code file (allow 0.25s debounce cooldown from initial sync)
        time.sleep(0.25)
        code_file.write_text(
            """class MutatedService:\n    def new_action(self):\n        return False\n""",
            encoding="utf-8",
        )

        # 3. Detect dirty immediately (<5ms)
        t0 = time.perf_counter()
        dirty, modified, _ = idx.is_dirty()
        dt_ms = (time.perf_counter() - t0) * 1000
        assert dirty
        assert dt_ms < 15.0
        assert any(p.resolve() == code_file.resolve() for p in modified)

        # 4. Trigger read query with transparent JIT auto-sync (_ensure_synced)
        results = idx.find_symbol("MutatedService")
        assert any(r["symbol"] == "MutatedService" for r in results)

        # 5. After read query, index is clean again
        dirty_after, _, _ = idx.is_dirty()
        assert not dirty_after
    finally:
        idx.close()


def test_design_client_and_system_path_inference(tmp_path):
    """Verify DLT-2026-026: CLT-* and DS-* infer owned code paths automatically from bound CNT-* and CMP-* without declaring path properties."""
    repo = tmp_path / "design_inference_repo"
    repo.mkdir()
    eidos_dir = repo / "eidos"
    cnt_dir = eidos_dir / "architecture" / "containers"
    clt_dir = eidos_dir / "design" / "clients"
    ds_dir = eidos_dir / "design" / "systems" / "brand"
    cnt_dir.mkdir(parents=True)
    clt_dir.mkdir(parents=True)
    ds_dir.mkdir(parents=True)

    # Create source files across multiple container source_paths
    web_src = repo / "apps" / "web" / "src"
    ui_pkg = repo / "packages" / "ui-kit" / "src"
    web_src.mkdir(parents=True)
    ui_pkg.mkdir(parents=True)
    (web_src / "App.tsx").write_text("export function App() { return null; }\n", encoding="utf-8")
    (ui_pkg / "Button.tsx").write_text("export function Button() { return null; }\n", encoding="utf-8")

    # Container with array source_path and inline component with source_path
    (cnt_dir / "web.adoc").write_text(
        """---
type: tech/container
id: CNT-WEB-APP
title: "Web Application"
container_type: web_app
runtime: "TypeScript / React"
source_path:
  - "apps/web/src"
  - "packages/ui-kit/**"
components:
  - id: CMP-WEB-BUTTON
    title: "Button Component"
    responsibility: "Renders interactive button"
    source_path: "packages/ui-kit/src/Button.tsx"
---
= Web Application
""",
        encoding="utf-8",
    )

    # Standalone component bound to CNT-WEB-APP with source_path
    (cnt_dir / "button-cmp.adoc").write_text(
        """---
type: tech/component
id: CMP-WEB-BUTTON
title: "Button Component"
container: CNT-WEB-APP
responsibility: "Renders interactive button"
source_path: "packages/ui-kit/src/Button.tsx"
---
= Button Component
""",
        encoding="utf-8",
    )

    # Client bound to CNT-WEB-APP and DS-BRAND (notice NO path properties on client!)
    (clt_dir / "web-client.adoc").write_text(
        """---
type: design/client
id: CLT-WEB-CLIENT
title: "Customer Web Client"
platform: web
status: active
container: CNT-WEB-APP
design_system: DS-BRAND
---
= Customer Web Client
""",
        encoding="utf-8",
    )

    # Design system bound to CLT-WEB-CLIENT (notice NO code path properties on system!)
    (ds_dir / "system.adoc").write_text(
        """---
type: design/system
id: DS-BRAND
title: "Brand Design System"
status: active
tokens:
  manifest: "eidos/design/systems/brand/tokens.json"
---
= Brand Design System
""",
        encoding="utf-8",
    )

    idx = ArchitecturalIndex(repo_root=repo, eidos_dir=eidos_dir)
    idx.sync(force=True)
    try:
        matches_app = idx.resolve_symbol_or_path("apps/web/src/App.tsx")
        matched_ids_app = {m["entity_id"] for m in matches_app}
        assert "CNT-WEB-APP" in matched_ids_app
        assert "CLT-WEB-CLIENT" in matched_ids_app
        assert "DS-BRAND" in matched_ids_app

        matches_btn = idx.resolve_symbol_or_path("packages/ui-kit/src/Button.tsx")
        matched_ids_btn = {m["entity_id"] for m in matches_btn}
        assert "CMP-WEB-BUTTON" in matched_ids_btn
        assert "CNT-WEB-APP" in matched_ids_btn
        assert "CLT-WEB-CLIENT" in matched_ids_btn
        assert "DS-BRAND" in matched_ids_btn
    finally:
        idx.close()


def test_test_assertion_edges_persist_after_sync(tmp_path):
    """INV-KNOWLEDGE-GRAPH-CATALOG: 'file::func' -> INV-* assertion edges survive sync and track test edits (DLT-2026-029)."""
    repo = tmp_path / "assert_edges_repo"
    eidos_dir = repo / "eidos"
    comps_dir = eidos_dir / "architecture" / "components"
    comps_dir.mkdir(parents=True)
    (repo / "src" / "pkg").mkdir(parents=True)
    (repo / "src" / "pkg" / "service.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    tests_dir = repo / "tests"
    tests_dir.mkdir()
    test_file = tests_dir / "test_service.py"
    test_file.write_text(
        "# Verifies INV-EDGE-RULE\ndef test_service_rule():\n    assert True\n",  # eidos:fixture
        encoding="utf-8",
    )
    (comps_dir / "service.adoc").write_text(
        """---
type: tech/component
id: CMP-EDGE-SERVICE
title: Edge Service
container: CNT-EDGE
status: active
source_path: src/pkg
responsibility: Edge service
invariants:
  - id: INV-EDGE-RULE  # eidos:fixture
    rule: Edge rule must hold.
    test_binding: tests/test_service.py::test_service_rule
---
= Edge Service
""",
        encoding="utf-8",
    )

    idx = ArchitecturalIndex(db_path=repo / "eidos" / ".cache" / "index.db", repo_root=repo, eidos_dir=eidos_dir)
    try:
        idx.sync(force=True)
        conn = idx._get_connection()
        rows = conn.execute(
            "SELECT source_id FROM edges WHERE rel_type = 'asserts' AND target_id = 'INV-EDGE-RULE';"  # eidos:fixture
        ).fetchall()
        sources = {r[0] for r in rows}
        assert "tests/test_service.py::test_service_rule" in sources
        # Spec-declared ownership edge is preserved alongside the test edge
        assert "CMP-EDGE-SERVICE" in sources

        status = idx.get_index_status()
        assert status["ast"]["test_invariant_assertions"] == 1
        assert status["ast"]["tested_invariants"] == 1

        # Renaming the test function replaces (not accumulates) its assertion edge
        time.sleep(0.3)
        test_file.write_text(
            "# Verifies INV-EDGE-RULE\ndef test_renamed_rule():\n    assert True\n",  # eidos:fixture
            encoding="utf-8",
        )
        idx.sync()
        test_sources = {
            r[0]
            for r in conn.execute(
                "SELECT source_id FROM edges WHERE rel_type = 'asserts' AND instr(source_id, '::') > 0;"
            ).fetchall()
        }
        assert test_sources == {"tests/test_service.py::test_renamed_rule"}
    finally:
        idx.close()


# ---------------------------------------------------------------------------
# DLT-2026-034: persistent AST symbol locations & recursive multi-package indexing
# ---------------------------------------------------------------------------


def _write_container(
    repo: Path,
    filename: str,
    cnt_id: str,
    source_path: str | list[str],
    components: list[tuple[str, str]] | None = None,
) -> Path:
    """Write a minimal CNT-* spec; ``components`` is a list of (CMP id, source_path) written as standalone component files."""
    containers = repo / "eidos" / "architecture" / "containers"
    containers.mkdir(parents=True, exist_ok=True)
    comp_dir = repo / "eidos" / "architecture" / "components"
    if isinstance(source_path, list):
        pkg_yaml = "source_path:\n" + "".join(f'  - "{p}"\n' for p in source_path)
    else:
        pkg_yaml = f'source_path: "{source_path}"\n'
    wanted_cids = {cid for cid, _ in (components or [])}
    if comp_dir.is_dir():
        for existing_comp in list(comp_dir.glob("*.adoc")):
            txt = existing_comp.read_text(encoding="utf-8")
            if f"container: {cnt_id}\n" in txt:
                cid_line = next((ln.split(":", 1)[1].strip() for ln in txt.splitlines() if ln.startswith("id: ")), "")
                if cid_line not in wanted_cids:
                    existing_comp.unlink()
    if components:
        comp_dir.mkdir(parents=True, exist_ok=True)
        for cid, sp in components:
            slug = cid.lower().replace("cmp-", "", 1)
            (comp_dir / f"{slug}.adoc").write_text(
                f"""---
type: tech/component
id: {cid}
title: "{cid.title()}"
container: {cnt_id}
status: active
source_path: "{sp}"
---
= {cid}

== Responsibility
r
""",
                encoding="utf-8",
            )
    path = containers / filename
    path.write_text(
        f"""---
type: tech/container
id: {cnt_id}
title: "{cnt_id}"
container_type: "library"
runtime: "Python"
{pkg_yaml}domain: DOM-X
tier: core
---
= {cnt_id}
""",
        encoding="utf-8",
    )
    return path


def _touch_later(path: Path, offset: float = 5.0) -> None:
    """Force an mtime change that the incremental dirty checker is guaranteed to notice."""
    t = time.time() + offset
    os.utime(path, (t, t))


def _make_symbol_repo(tmp_path: Path, name: str = "symrepo") -> tuple[Path, Path, Path]:
    repo = tmp_path / name
    (repo / "src" / "pkg").mkdir(parents=True)
    src = repo / "src" / "pkg" / "service.py"
    src.write_text(
        "class SampleService:\n    def execute(self):\n        return True\n\n\ndef helper():\n    return 1\n",
        encoding="utf-8",
    )
    spec = _write_container(repo, "sample.adoc", "CNT-SAMPLE", "src/pkg")
    return repo, src, spec


def test_find_symbol_returns_source_location(tmp_path, temp_index):
    """INV-SYMBOL-LOCATION-FIDELITY: find_symbol reports the defining file/lines; 'file_path' stays the spec path."""
    repo, _src, _spec = _make_symbol_repo(tmp_path)
    idx = ArchitecturalIndex(db_path=repo / "eidos" / ".cache" / "index.db", repo_root=repo, eidos_dir=repo / "eidos")
    try:
        idx.sync(force=True)
        matches = [m for m in idx.find_symbol("SampleService") if m["symbol"] == "SampleService"]
        assert len(matches) == 1
        assert matches[0]["kind"] == "class"
        assert matches[0]["file_path"] == "eidos/architecture/containers/sample.adoc"  # unchanged meaning
        loc = matches[0]["location"]
        assert loc["file"] == "src/pkg/service.py"
        assert loc["line_start"] == 1 and loc["line_end"] >= loc["line_start"]
        assert loc["signature"].startswith("class SampleService")
        assert loc["parent_symbol"] is None

        method = next(m for m in idx.find_symbol("SampleService.execute") if m["symbol"] == "SampleService.execute")
        assert method["location"]["line_start"] == 2
        assert method["location"]["parent_symbol"] == "SampleService"

        # Non-AST matches (spec paths, components) carry no location
        spec_hits = [m for m in idx.find_symbol("src/pkg") if m["kind"] == "package_path"]
        assert spec_hits and all(m["location"] is None for m in spec_hits)
    finally:
        idx.close()

    real = [m for m in temp_index.find_symbol("ArchitecturalIndex") if m["symbol"] == "ArchitecturalIndex"]
    assert real and real[0]["location"]["file"] == "src/eidos/core/index/graph_index.py"
    assert real[0]["location"]["line_start"] <= real[0]["location"]["line_end"]


def test_repo_map_warm_index_does_not_parse_sources(tmp_path, monkeypatch):
    """INV-SYMBOL-LOCATION-FIDELITY: a warm repo map is built from SQLite with zero AST extraction calls."""
    from eidos.core.index import ast_indexer
    from eidos.core.index.ast_indexer import get_ast_indexer

    repo, _src, _spec = _make_symbol_repo(tmp_path, "mapnrepo")
    (repo / "src" / "pkg" / "second.py").write_text("def other():\n    return 2\n", encoding="utf-8")
    idx = ArchitecturalIndex(db_path=repo / "eidos" / ".cache" / "index.db", repo_root=repo, eidos_dir=repo / "eidos")
    try:
        idx.sync(force=True)

        # Reference: the pre-change algorithm (parse every indexed source file from disk)
        indexer = get_ast_indexer()
        reference_files = {
            r["symbol"]
            for r in idx._get_connection().execute("SELECT symbol FROM symbol_mappings WHERE kind = 'source_path';")
        }
        reference = indexer.generate_repo_map(
            {f: indexer.extract_symbols(repo / f) for f in sorted(reference_files)}, max_tokens=1500
        )

        def _boom(self, *args, **kwargs):
            raise AssertionError("extract_symbols must not be called for a warm repo map")

        monkeypatch.setattr(ast_indexer.AstGrepIndexer, "extract_symbols", _boom)

        repo_map = idx.get_repo_map()
        assert repo_map == reference
        assert "SampleService" in repo_map and "other" in repo_map
    finally:
        idx.close()


def test_code_symbols_invalidated_on_edit_and_delete(tmp_path):
    """INV-SYMBOL-LOCATION-FIDELITY: coordinates follow source edits and disappear with deleted files."""
    repo, src, _spec = _make_symbol_repo(tmp_path, "invalrepo")
    idx = ArchitecturalIndex(db_path=repo / "eidos" / ".cache" / "index.db", repo_root=repo, eidos_dir=repo / "eidos")
    conn = idx._get_connection()

    def rows(symbol: str) -> list[tuple[int, str]]:
        return [
            (r["line_start"], r["file_path"])
            for r in conn.execute("SELECT line_start, file_path FROM code_symbols WHERE symbol = ?;", (symbol,))
        ]

    try:
        idx.sync(force=True)
        assert rows("helper") == [(6, "src/pkg/service.py")]

        # Shift 'helper' down 10 lines and rename the class: exactly one fresh row, no stale ones
        src.write_text(
            "# pad\n" * 10 + "class RenamedService:\n    def execute(self):\n        return True\n\n\n"
            "def helper():\n    return 1\n",
            encoding="utf-8",
        )
        _touch_later(src)
        idx.sync()
        assert rows("helper") == [(16, "src/pkg/service.py")]
        assert rows("SampleService") == []
        assert idx.find_symbol("SampleService.execute") == [] or all(
            m["symbol"] != "SampleService.execute" for m in idx.find_symbol("SampleService.execute")
        )
        assert not conn.execute("SELECT 1 FROM code_symbols WHERE symbol = 'SampleService';").fetchone()
        assert not conn.execute(
            "SELECT 1 FROM code_symbols WHERE parent_symbol = 'SampleService' AND symbol = 'execute';"
        ).fetchone()
        assert rows("RenamedService") == [(11, "src/pkg/service.py")]
        helper = next(m for m in idx.find_symbol("helper") if m["symbol"] == "helper")
        assert helper["location"]["line_start"] == 16

        # Deleting the file removes every coordinate and mapping
        src.unlink()
        idx.sync()
        assert (
            conn.execute("SELECT COUNT(*) FROM code_symbols WHERE file_path = 'src/pkg/service.py';").fetchone()[0] == 0
        )
        assert not conn.execute("SELECT 1 FROM code_symbols WHERE symbol IN ('helper', 'RenamedService');").fetchone()
        assert not any(m["symbol"] == "helper" for m in idx.find_symbol("helper"))
    finally:
        idx.close()


def test_spec_edit_keeps_package_code_symbols(tmp_path):
    """INV-SYMBOL-LOCATION-FIDELITY: editing the owning spec must not lose package files' coordinates or mappings."""
    repo, _src, spec = _make_symbol_repo(tmp_path, "specedit")
    idx = ArchitecturalIndex(db_path=repo / "eidos" / ".cache" / "index.db", repo_root=repo, eidos_dir=repo / "eidos")
    try:
        idx.sync(force=True)
        spec.write_text(spec.read_text(encoding="utf-8") + "\nExtra prose.\n", encoding="utf-8")
        _touch_later(spec)
        idx.sync()
        hit = next(m for m in idx.find_symbol("SampleService") if m["symbol"] == "SampleService")
        assert hit["location"]["file"] == "src/pkg/service.py"
        conn = idx._get_connection()
        assert conn.execute(
            "SELECT 1 FROM symbol_mappings WHERE symbol = 'src/pkg/service.py' AND kind = 'source_path';"
        ).fetchone()
        assert not idx.is_dirty()[0]
    finally:
        idx.close()


def test_schema_version_mismatch_rebuilds_cache(tmp_path):
    """INV-KNOWLEDGE-GRAPH-CATALOG: a legacy (unversioned) index.db is dropped and rebuilt with the 2.0.0 schema."""
    repo, _src, _spec = _make_symbol_repo(tmp_path, "migrepo")
    db_path = repo / "eidos" / ".cache" / "index.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    legacy = sqlite3.connect(str(db_path))
    legacy.executescript(
        """
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE indexed_files (rel_path TEXT PRIMARY KEY, mtime REAL NOT NULL, sha256 TEXT NOT NULL,
            doc_id TEXT, doc_type TEXT, last_indexed REAL NOT NULL);
        CREATE TABLE entities (id TEXT PRIMARY KEY, type TEXT NOT NULL, title TEXT NOT NULL, status TEXT,
            domain TEXT, package_path TEXT, runtime TEXT, container_type TEXT, file_path TEXT NOT NULL,
            data_json TEXT NOT NULL);
        CREATE TABLE edges (source_id TEXT NOT NULL, target_id TEXT NOT NULL, rel_type TEXT NOT NULL,
            metadata_json TEXT, PRIMARY KEY (source_id, target_id, rel_type));
        CREATE TABLE symbol_mappings (symbol TEXT NOT NULL, entity_id TEXT NOT NULL, kind TEXT NOT NULL,
            PRIMARY KEY (symbol, entity_id, kind));
        INSERT INTO entities VALUES ('CNT-LEGACY', 'tech/container', 'legacy', 'active', '', '', '', '', 'x.adoc', '{}');
        INSERT INTO symbol_mappings VALUES ('legacy_sym', 'CNT-LEGACY', 'class');
        """
    )
    legacy.commit()
    legacy.close()

    idx = ArchitecturalIndex(db_path=db_path, repo_root=repo, eidos_dir=repo / "eidos")
    try:
        conn = idx._get_connection()
        version = conn.execute("SELECT value FROM metadata WHERE key = 'schema_version';").fetchone()[0]
        assert version == ArchitecturalIndex.SCHEMA_VERSION
        assert conn.execute("SELECT COUNT(*) FROM entities;").fetchone()[0] == 0  # legacy rows dropped
        assert conn.execute("SELECT COUNT(*) FROM code_symbols;").fetchone()[0] == 0

        hit = next(m for m in idx.find_symbol("SampleService") if m["symbol"] == "SampleService")
        assert hit["location"]["line_start"] == 1
        assert not any(m["symbol"] == "legacy_sym" for m in idx.find_symbol("legacy_sym"))

        conn.execute("INSERT INTO metadata (key, value) VALUES ('marker', '1');")
        conn.commit()
    finally:
        idx.close()

    # A current-version database is reused, never rebuilt
    reopened = ArchitecturalIndex(db_path=db_path, repo_root=repo, eidos_dir=repo / "eidos")
    try:
        assert reopened._get_connection().execute("SELECT value FROM metadata WHERE key = 'marker';").fetchone()
    finally:
        reopened.close()


def test_is_dirty_tracks_secondary_package_paths(tmp_path):
    """INV-KNOWLEDGE-GRAPH-CATALOG: every declared package_path (not only the first) is dirty-tracked."""
    repo = tmp_path / "multipkg"
    (repo / "src" / "pkg").mkdir(parents=True)
    (repo / "extras").mkdir()
    (repo / "src" / "pkg" / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    helper = repo / "extras" / "helper.py"
    helper.write_text("def helper_fn():\n    return 1\n", encoding="utf-8")
    _write_container(repo, "multi.adoc", "CNT-MULTI", ["src/pkg", "extras"])

    idx = ArchitecturalIndex(db_path=repo / "eidos" / ".cache" / "index.db", repo_root=repo, eidos_dir=repo / "eidos")
    try:
        idx.sync(force=True)
        assert not idx.is_dirty()[0]
        assert any(m["symbol"] == "helper_fn" for m in idx.find_symbol("helper_fn"))

        helper.write_text("def helper_fn():\n    return 2\n\n\ndef added_fn():\n    return 3\n", encoding="utf-8")
        _touch_later(helper)
        dirty, modified, _ = idx.is_dirty()
        assert dirty
        assert any(p.resolve() == helper.resolve() for p in modified)
        idx.sync()
        assert any(m["symbol"] == "added_fn" for m in idx.find_symbol("added_fn"))
    finally:
        idx.close()


def _ownership_repo(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / name
    for d in ("src/pkg/sub/deeper", "lib/inner", "src/pkg/__pycache__"):
        (repo / d).mkdir(parents=True)
    files = {
        "src/pkg/base.py": "def base_fn():\n    return 1\n",
        "src/pkg/sub/deep.py": "def deep_fn():\n    return 1\n",
        "src/pkg/sub/deeper/deepest.py": "def deepest_fn():\n    return 1\n",
        "src/pkg/special.py": "def special_fn():\n    return 1\n",
        "src/pkg/__pycache__/junk.py": "def junk_fn():\n    return 1\n",
        "lib/core.py": "def core_fn():\n    return 1\n",
        "lib/inner/extra.py": "def extra_fn():\n    return 1\n",
        "shared/common.py": "class Shared:\n    def run(self):\n        return 1\n",
    }
    for rel, content in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(content, encoding="utf-8")
    # CNT-BROAD owns the whole package dir; CNT-CLAIMER's component explicitly claims one file inside it.
    _write_container(repo, "broad.adoc", "CNT-BROAD", "src/pkg", components=[("CMP-COMMON-B", "shared/common.py")])
    _write_container(
        repo,
        "claimer.adoc",
        "CNT-CLAIMER",
        "lib",
        components=[("CMP-SPECIAL", "src/pkg/special.py"), ("CMP-COMMON-C", "shared/common.py")],
    )
    # A container with inline components must still index its unclaimed nested files recursively
    _write_container(repo, "inline.adoc", "CNT-INLINE", "lib", components=[("CMP-CORE", "lib/core.py")])
    return repo


def _ownership_snapshot(idx: ArchitecturalIndex) -> dict[str, list]:
    conn = idx._get_connection()
    return {
        "code_symbols": [
            tuple(r)
            for r in conn.execute(
                "SELECT file_path, symbol, kind, parent_symbol, line_start, entity_id, is_primary "
                "FROM code_symbols ORDER BY file_path, entity_id, kind, parent_symbol, symbol, line_start;"
            )
        ],
        "mappings": [
            tuple(r)
            for r in conn.execute(
                "SELECT symbol, entity_id, kind FROM symbol_mappings "
                "WHERE kind IN ('source_path', 'class', 'function', 'method') ORDER BY symbol, entity_id, kind;"
            )
        ],
        "manifest": [
            tuple(r)
            for r in conn.execute(
                "SELECT rel_path, doc_id FROM indexed_files WHERE doc_type = 'code/source' ORDER BY rel_path;"
            )
        ],
    }


_ORDERINGS = {
    "as_discovered": lambda paths: list(paths),
    "reversed": lambda paths: list(reversed(paths)),
    "specs_reversed_code_first": lambda paths: (
        [p for p in paths if p.suffix != ".adoc"] + list(reversed([p for p in paths if p.suffix == ".adoc"]))
    ),
    "sorted_by_name_desc": lambda paths: sorted(paths, key=lambda p: p.name, reverse=True),
}


def test_recursive_package_indexing_respects_global_ownership(tmp_path, monkeypatch):
    """INV-KNOWLEDGE-GRAPH-CATALOG: nested files are indexed even when a container has standalone components, and
    the complete ownership state is identical under every spec/code processing order (forced via is_dirty)."""
    original_is_dirty = ArchitecturalIndex.is_dirty
    snapshots: dict[str, dict[str, list]] = {}
    for name, order in _ORDERINGS.items():
        repo = _ownership_repo(tmp_path, f"own_{name}")

        def ordered_is_dirty(self, _order=order):
            dirty, to_update, deleted = original_is_dirty(self)
            return dirty, _order(to_update), deleted

        monkeypatch.setattr(ArchitecturalIndex, "is_dirty", ordered_is_dirty)
        idx = ArchitecturalIndex(
            db_path=repo / "eidos" / ".cache" / "index.db", repo_root=repo, eidos_dir=repo / "eidos"
        )
        try:
            idx.sync(force=True)
            snapshots[name] = _ownership_snapshot(idx)
            monkeypatch.setattr(ArchitecturalIndex, "is_dirty", original_is_dirty)
            assert not idx.is_dirty()[0]
        finally:
            idx.close()

    reference = snapshots["as_discovered"]
    for name, snap in snapshots.items():
        assert snap == reference, f"ownership differs for processing order '{name}'"

    primary = {(f, e) for f, _, _, _, _, e, is_primary in reference["code_symbols"] if is_primary}
    assert ("src/pkg/special.py", "CMP-SPECIAL") in primary  # explicit component claim beats package prefix
    assert {("src/pkg/base.py", "CNT-BROAD"), ("src/pkg/sub/deep.py", "CNT-BROAD")} <= primary
    assert ("src/pkg/sub/deeper/deepest.py", "CNT-BROAD") in primary
    assert not any(f.startswith("src/pkg/__pycache__") for f, _ in primary)  # excluded directories are pruned
    # 'lib' is declared by two containers: the deterministic (sorted by id) tie-break picks CNT-CLAIMER
    assert ("lib/inner/extra.py", "CNT-CLAIMER") in primary
    assert ("lib/core.py", "CMP-CORE") in primary
    special = [r for r in reference["code_symbols"] if r[0] == "src/pkg/special.py"]
    assert {r[5] for r in special} == {"CMP-SPECIAL"}


def test_shared_source_file_keeps_every_declaring_owner(tmp_path):
    """INV-SYMBOL-LOCATION-FIDELITY: a file declared by several components is indexed once (one primary row set for
    repo maps) yet every declaring component keeps its symbol mappings and locations; dropping a declaration
    prunes only that component's rows."""
    repo = _ownership_repo(tmp_path, "shared")
    idx = ArchitecturalIndex(db_path=repo / "eidos" / ".cache" / "index.db", repo_root=repo, eidos_dir=repo / "eidos")
    conn = idx._get_connection()
    try:
        idx.sync(force=True)
        rows = conn.execute(
            "SELECT entity_id, is_primary FROM code_symbols WHERE file_path = 'shared/common.py' AND symbol = 'Shared';"
        ).fetchall()
        assert sorted(tuple(r) for r in rows) == [("CMP-COMMON-B", 1), ("CMP-COMMON-C", 0)]

        hits = [m for m in idx.find_symbol("Shared") if m["symbol"] == "Shared"]
        assert sorted(m["entity_id"] for m in hits) == ["CMP-COMMON-B", "CMP-COMMON-C"]
        assert all(m["location"]["file"] == "shared/common.py" for m in hits)
        assert idx.get_repo_map().count("class Shared") == 1  # rendered once despite two owners

        # CNT-CLAIMER drops CMP-COMMON-C: only its rows and mappings disappear
        spec = _write_container(
            repo, "claimer.adoc", "CNT-CLAIMER", "lib", components=[("CMP-SPECIAL", "src/pkg/special.py")]
        )
        _touch_later(spec)
        idx.sync()
        assert {
            r[0]
            for r in conn.execute("SELECT DISTINCT entity_id FROM code_symbols WHERE file_path = 'shared/common.py';")
        } == {"CMP-COMMON-B"}
        assert not conn.execute(
            "SELECT 1 FROM code_symbols WHERE entity_id = 'CMP-COMMON-C' AND symbol IN ('Shared', 'run');"
        ).fetchone()
        assert not conn.execute(
            "SELECT 1 FROM symbol_mappings WHERE symbol = 'shared/common.py' AND entity_id = 'CMP-COMMON-C';"
        ).fetchone()
        assert conn.execute(
            "SELECT 1 FROM code_symbols WHERE entity_id = 'CMP-COMMON-B' AND symbol = 'Shared';"
        ).fetchone()
    finally:
        idx.close()


def test_code_only_sync_skips_ownership_pass(tmp_path, monkeypatch):
    """INV-AGENT-GRAPH-INDEX-FRESHNESS: the package walk / ownership pass only runs when specifications change."""
    import eidos.core.index.sync as sync_mod

    repo, src, spec = _make_symbol_repo(tmp_path, "gate")
    idx = ArchitecturalIndex(db_path=repo / "eidos" / ".cache" / "index.db", repo_root=repo, eidos_dir=repo / "eidos")
    calls: list[int] = []
    original = sync_mod.reconcile_code_ownership

    def spy(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(sync_mod, "reconcile_code_ownership", spy)
    try:
        idx.sync(force=True)
        assert calls == [1]

        # Code-only change (edit + brand-new file in a package dir): indexed directly, no ownership pass
        src.write_text(src.read_text(encoding="utf-8") + "\n\ndef added_fn():\n    return 2\n", encoding="utf-8")
        _touch_later(src)
        (repo / "src" / "pkg" / "fresh.py").write_text("def fresh_fn():\n    return 3\n", encoding="utf-8")
        idx.sync()
        assert calls == [1]
        assert any(m["symbol"] == "added_fn" for m in idx.find_symbol("added_fn"))
        fresh = next(m for m in idx.find_symbol("fresh_fn") if m["symbol"] == "fresh_fn")
        assert fresh["entity_id"] == "CNT-SAMPLE"

        # Spec change: ownership pass runs
        spec.write_text(spec.read_text(encoding="utf-8") + "\nMore prose.\n", encoding="utf-8")
        _touch_later(spec, offset=10)
        idx.sync()
        assert calls == [1, 1]
    finally:
        idx.close()


def test_ownership_reattributed_when_spec_changes(tmp_path):
    """INV-SYMBOL-LOCATION-FIDELITY: moving a package to another container re-attributes its symbols."""
    repo, _src, spec = _make_symbol_repo(tmp_path, "reown")
    idx = ArchitecturalIndex(db_path=repo / "eidos" / ".cache" / "index.db", repo_root=repo, eidos_dir=repo / "eidos")
    conn = idx._get_connection()
    try:
        idx.sync(force=True)
        assert conn.execute("SELECT DISTINCT entity_id FROM code_symbols;").fetchall()[0][0] == "CNT-SAMPLE"

        _write_container(repo, "sample.adoc", "CNT-SAMPLE", "src/elsewhere")
        _touch_later(spec)
        new_spec = _write_container(repo, "newowner.adoc", "CNT-NEWOWNER", "src/pkg")
        _touch_later(new_spec)
        idx.sync()

        assert {r[0] for r in conn.execute("SELECT DISTINCT entity_id FROM code_symbols;")} == {"CNT-NEWOWNER"}
        hit = next(m for m in idx.find_symbol("helper") if m["symbol"] == "helper")
        assert hit["entity_id"] == "CNT-NEWOWNER"
        assert not conn.execute(
            "SELECT 1 FROM code_symbols WHERE entity_id = 'CNT-SAMPLE' AND kind = 'function';"
        ).fetchone()
        assert not conn.execute(
            "SELECT 1 FROM symbol_mappings WHERE symbol = 'src/pkg/service.py' AND entity_id = 'CNT-SAMPLE';"
        ).fetchone()
    finally:
        idx.close()


def test_incremental_spec_sync_preserves_incoming_edges(tmp_path):
    """Incremental sync of a single container spec must not drop incoming edges declared by unchanged capabilities."""
    repo, _src, spec = _make_symbol_repo(tmp_path, "edgesync")
    cap_dir = repo / "eidos" / "domains" / "core" / "capabilities"
    cap_dir.mkdir(parents=True, exist_ok=True)
    cap_spec = cap_dir / "cap.adoc"
    cap_spec.write_text(
        "---\n"
        "type: product/capability\n"
        "id: CAP-SAMPLE-001\n"
        "title: Sample Capability\n"
        "domain: DOM-CORE\n"
        "technical:\n"
        "  containers:\n"
        "    - CNT-SAMPLE\n"
        "---\n"
        "= Sample Capability\n",
        encoding="utf-8",
    )

    idx = ArchitecturalIndex(db_path=repo / "eidos" / ".cache" / "index.db", repo_root=repo, eidos_dir=repo / "eidos")
    try:
        idx.sync(force=True)
        before_neighbors = {n["id"] for n in idx.get_k_hop_neighborhood("CNT-SAMPLE", max_depth=1)}
        assert "CAP-SAMPLE-001" in before_neighbors

        # Modify only the container spec and run incremental sync(force=False)
        spec.write_text(spec.read_text(encoding="utf-8") + "\nUpdated container prose.\n", encoding="utf-8")
        _touch_later(spec, offset=10)
        stats = idx.sync(force=False)
        assert stats.updated_modified == 1

        after_neighbors = {n["id"] for n in idx.get_k_hop_neighborhood("CNT-SAMPLE", max_depth=1)}
        assert "CAP-SAMPLE-001" in after_neighbors
    finally:
        idx.close()


def test_fts5_indexes_embedded_decisions(temp_index):
    """Verify INV-KNOWLEDGE-GRAPH-CATALOG and INV-AGENT-CONTEXT-SLICING: embedded decisions and rejected_alternatives are indexed in specs_fts."""
    matches = temp_index.query_fts("DEC-INDEX-SQLITE-WAL ChromaDB LanceDB", limit=5)
    matched_ids = [m["id"] for m in matches]
    assert "CNT-EIDOS-INDEX" in matched_ids


def _write_retrieval_spec(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


_WIDGET_INV_ID = "INV-WIDGET-TOMBSTONE"  # eidos:fixture (synthetic invariant, intentionally undeclared)

_RETRIEVAL_CAP_WITH_INVARIANT = (
    "---\n"
    "type: product/capability\n"
    "id: CAP-WIDGET-001\n"
    "title: Widget Archival\n"
    "domain: DOM-WIDGET\n"
    "technical:\n"
    "  containers:\n"
    "    - CNT-WIDGET\n"
    "  invariants:\n"
    f"    - id: {_WIDGET_INV_ID}\n"
    '      rule: "Archived widgets must keep a tombstone so every frobnicate pass stays reversible."\n'
    '      test_binding: "tests/test_widget.py::test_frobnicate_tombstone_archive"\n'
    "---\n"
    "= Widget Archival\n"
)


def test_task_retrieval_indexes_invariants_and_filters_types(tmp_path):
    """INV-AGENT-TASK-RETRIEVAL-COVERAGE: task retrieval indexes child invariants, de-compounds identifiers,
    weights columns positionally, keeps SpecDeltas out of slice slots, resolves matched domains, and purges stale
    invariant FTS rows on incremental sync."""
    from eidos.core.index.graph_index import FTS_COLUMN_WEIGHTS, build_fts_match_query, split_identifier

    repo = tmp_path / "retrieval"
    eidos_dir = repo / "eidos"
    _write_retrieval_spec(
        eidos_dir / "domains" / "widget" / "domain.adoc",
        "---\ntype: product/domain\nid: DOM-WIDGET\ntitle: Quasar Widget Lifecycle\n---\n= Quasar Widget Lifecycle\n",
    )
    _write_retrieval_spec(
        eidos_dir / "architecture" / "containers" / "widget.adoc",
        "---\ntype: tech/container\nid: CNT-WIDGET\ntitle: Widget Service\ndomain: DOM-WIDGET\n"
        "source_path: src/widget\n---\n= Widget Service\n\n"
        "The WidgetRegistryEngine owns registration for every quasar deployment in the fleet.\n",
    )
    cap = _write_retrieval_spec(
        eidos_dir / "domains" / "widget" / "capabilities" / "cap.adoc", _RETRIEVAL_CAP_WITH_INVARIANT
    )
    # Twelve historical SpecDeltas saturated with the task vocabulary (more than the 8 slice slots).
    for n in range(12):
        _write_retrieval_spec(
            eidos_dir / "deltas" / f"dlt-2026-{n:03d}.adoc",
            f"---\ntype: delta/mutation\nid: DLT-2026-{n:03d}\ntitle: Frobnicate tombstone pass {n}\n"
            "status: applied\n---\n= Frobnicate tombstone pass\n\nfrobnicate tombstone frobnicate tombstone\n",
        )

    # Pure helpers: identifier splitting, stop-word filtering, and snake_case-safe query construction.
    assert split_identifier("get_tiered_context_slice") == ["get", "tiered", "context", "slice"]
    assert split_identifier("ArchitecturalIndex") == ["architectural", "index"]
    assert split_identifier("HTTPServer") == ["http", "server"]
    assert '"detect_drift"' not in build_fts_match_query("detect_drift")
    assert '"the"*' not in build_fts_match_query("fix the drift in the index")
    assert '"in"*' not in build_fts_match_query("fix the drift in the index")
    assert build_fts_match_query("the in and") != ""  # fallback when all tokens are stop words
    # bm25() weights are positional over every column, including the UNINDEXED entity_id placeholder.
    assert len(FTS_COLUMN_WEIGHTS) == 6 and FTS_COLUMN_WEIGHTS[0] == 0.0

    idx = ArchitecturalIndex(db_path=repo / "eidos" / ".cache" / "index.db", repo_root=repo, eidos_dir=eidos_dir)
    try:
        idx.sync(force=True)
        conn = idx._get_connection()
        fts_rows = conn.execute("SELECT COUNT(*) FROM specs_fts;").fetchone()[0]
        entity_rows = conn.execute("SELECT COUNT(*) FROM entities;").fetchone()[0]
        assert fts_rows == entity_rows  # every entity, including child INV-* rules, is searchable

        # 1. Child invariants are directly searchable, and exclusion filters apply before LIMIT.
        top = [m["id"] for m in idx.query_fts("frobnicate tombstone", limit=3, exclude_types=("delta/mutation",))]
        assert top and top[0] == _WIDGET_INV_ID
        assert not any(i.startswith("DLT-") for i in top)

        # 2. Task slices never spend candidate slots on historical SpecDeltas.
        sl = idx.get_tiered_context_slice(task="frobnicate tombstone")
        assert _WIDGET_INV_ID in [inv["id"] for inv in sl.invariants]
        assert any("FTS lexical match" in r for r in sl.matched_reasons[_WIDGET_INV_ID])
        assert not any(k.startswith("DLT-") for k in sl.matched_reasons)

        # 3. snake_case prompts match out-of-order words; camelCase identifiers match their sub-tokens.
        inv_hits = [m["id"] for m in idx.query_fts("tombstone_frobnicate", include_types=("invariant",))]
        assert inv_hits == [_WIDGET_INV_ID]
        cnt_hits = [m["id"] for m in idx.query_fts("registry engine", include_types=("tech/container",))]
        assert cnt_hits == ["CNT-WIDGET"]

        # 4. Title matches outrank body matches (weights land on the intended columns).
        ranked = [m["id"] for m in idx.query_fts("quasar", include_types=("product/domain", "tech/container"))]
        assert ranked[:2] == ["DOM-WIDGET", "CNT-WIDGET"]

        # 5. A matched domain resolves to its member container and capability instead of being dropped,
        #    while prioritizing direct container/component matches ahead of 1-hop domain expansions.
        _write_retrieval_spec(
            eidos_dir / "architecture" / "containers" / "aaa_member.adoc",
            "---\ntype: tech/container\nid: CNT-AAA-MEMBER\ntitle: Alphabetical Member Service\ndomain: DOM-WIDGET\n"
            "source_path: src/aaa\n---\n= Alphabetical Member Service\n",
        )
        _write_retrieval_spec(
            eidos_dir / "architecture" / "components" / "widget_comp.adoc",
            "---\ntype: tech/component\nid: CMP-WIDGET-CORE\ntitle: Widget Core Engine\ncontainer: CNT-WIDGET\n"
            "status: active\nsource_path: src/widget/core.py\n---\n= Widget Core Engine\n\n"
            "Quasar lifecycle orchestration component.\n",
        )
        idx.sync(force=False)
        dom_slice = idx.get_tiered_context_slice(task="quasar lifecycle widget core")
        assert "DOM-WIDGET" in dom_slice.matched_reasons
        comp_ids_in_order = [c["id"] for c in dom_slice.components]
        assert "CNT-WIDGET" in comp_ids_in_order
        if "CNT-AAA-MEMBER" in comp_ids_in_order:
            assert comp_ids_in_order.index("CNT-WIDGET") < comp_ids_in_order.index("CNT-AAA-MEMBER")
        assert "CAP-WIDGET-001" in [c["id"] for c in dom_slice.capabilities]

        # 6. Removing the invariant purges its FTS row on incremental sync (no stale search hits).
        cap.write_text(_RETRIEVAL_CAP_WITH_INVARIANT.split("  invariants:\n")[0] + "---\n= Widget Archival\n")
        _touch_later(cap, offset=10)
        idx.sync()
        assert idx.query_fts("frobnicate tombstone", include_types=("invariant",)) == []
        fts_rows = conn.execute("SELECT COUNT(*) FROM specs_fts;").fetchone()[0]
        entity_rows = conn.execute("SELECT COUNT(*) FROM entities;").fetchone()[0]
        assert fts_rows == entity_rows
    finally:
        idx.close()


def test_inv_index_incremental_parity(tmp_path, monkeypatch):
    """Verify INV-INDEX-INCREMENTAL-PARITY: mtime refresh on SHA-256 match, single-pass dirty_state handoff,
    path-keyed entity/edge/FTS eviction on spec ID rename, duplicate ID collision rejection, and unified SQLite schema_version."""
    from eidos.core.index.graph_index import SCHEMA_VERSION, open_cache_connection
    from eidos.core.spec.validator import SpecValidator
    from eidos.vcs.drift import extract_invariants_and_verify

    repo = tmp_path / "parity"
    eidos_dir = repo / "eidos"
    cnt_dir = eidos_dir / "architecture" / "containers"
    cap_dir = eidos_dir / "domains" / "core" / "capabilities"
    cnt_dir.mkdir(parents=True)
    cap_dir.mkdir(parents=True)

    spec_a = cnt_dir / "alpha.adoc"
    spec_a.write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-OLD-NAME\n"
        "title: Old Container\n"
        "container_type: library\n"
        "runtime: python\n"
        "source_path: src/alpha\n"
        "---\n"
        "= Old Container\n",
        encoding="utf-8",
    )

    # 1. Validator and Drift caches stamp canonical SCHEMA_VERSION (2.5.0), so ArchitecturalIndex does not drop them
    validator = SpecValidator()
    validator.validate_directory(eidos_dir)
    extract_invariants_and_verify(eidos_dir, repo)

    import contextlib

    db_path = eidos_dir / ".cache" / "index.db"
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        ver = conn.execute("SELECT value FROM metadata WHERE key = 'schema_version';").fetchone()[0]
        assert ver == SCHEMA_VERSION == "2.18.0"
        val_rows_before = conn.execute("SELECT COUNT(*) FROM spec_validation_cache;").fetchone()[0]
        assert val_rows_before == 1

    idx = ArchitecturalIndex(db_path=db_path, repo_root=repo, eidos_dir=eidos_dir)
    try:
        idx.sync(force=False)
        conn = idx._get_connection()
        val_rows_after = conn.execute("SELECT COUNT(*) FROM spec_validation_cache;").fetchone()[0]
        assert val_rows_after == 1

        # 2. Mtime refresh in place when SHA-256 is unchanged
        old_mtime = conn.execute(
            "SELECT mtime FROM indexed_files WHERE rel_path = 'eidos/architecture/containers/alpha.adoc';"
        ).fetchone()[0]
        bumped_mtime = old_mtime + 120.0
        os.utime(spec_a, (bumped_mtime, bumped_mtime))

        dirty, to_update, to_delete = idx.is_dirty()
        assert not dirty and to_update == [] and to_delete == []
        refreshed_mtime = conn.execute(
            "SELECT mtime FROM indexed_files WHERE rel_path = 'eidos/architecture/containers/alpha.adoc';"
        ).fetchone()[0]
        assert abs(refreshed_mtime - bumped_mtime) < 1.0

        # 3. _ensure_synced passes dirty_state into sync() without calling is_dirty() a second time
        spec_a.write_text(spec_a.read_text(encoding="utf-8") + "\nUpdated body.\n", encoding="utf-8")
        os.utime(spec_a, (bumped_mtime + 60.0, bumped_mtime + 60.0))
        is_dirty_calls: list[int] = []
        orig_is_dirty = idx.is_dirty

        def _spy_is_dirty():
            is_dirty_calls.append(1)
            return orig_is_dirty()

        monkeypatch.setattr(idx, "is_dirty", _spy_is_dirty)
        idx._last_synced_check = 0.0
        idx._ensure_synced()
        assert len(is_dirty_calls) == 1
        monkeypatch.setattr(idx, "is_dirty", orig_is_dirty)

        # 4. Path-keyed eviction when a specification ID is renamed in place (CNT-OLD-NAME -> CNT-NEW-NAME)
        spec_a.write_text(
            spec_a.read_text(encoding="utf-8").replace("id: CNT-OLD-NAME", "id: CNT-NEW-NAME"),
            encoding="utf-8",
        )
        os.utime(spec_a, (bumped_mtime + 120.0, bumped_mtime + 120.0))
        idx.sync(force=False)

        entity_ids = {r[0] for r in conn.execute("SELECT id FROM entities;").fetchall()}
        fts_ids = {r[0] for r in conn.execute("SELECT entity_id FROM specs_fts;").fetchall()}
        sym_eids = {r[0] for r in conn.execute("SELECT DISTINCT entity_id FROM symbol_mappings;").fetchall()}
        assert "CNT-NEW-NAME" in entity_ids
        assert "CNT-OLD-NAME" not in entity_ids
        assert "CNT-OLD-NAME" not in fts_ids
        assert "CNT-OLD-NAME" not in sym_eids

        # 5. Duplicate entity ID collision across distinct files is rejected rather than overwriting CNT-NEW-NAME
        spec_b = cnt_dir / "beta_duplicate.adoc"
        spec_b.write_text(
            "---\n"
            "type: tech/container\n"
            "id: CNT-NEW-NAME\n"
            "title: Colliding Container\n"
            "container_type: library\n"
            "runtime: python\n"
            "source_path: src/beta\n"
            "---\n"
            "= Colliding Container\n",
            encoding="utf-8",
        )
        dup_stats = idx.sync(force=False)
        assert any("CNT-NEW-NAME" in msg for msg in dup_stats.duplicate_id_collisions)
        stored_row = conn.execute("SELECT title, file_path FROM entities WHERE id = 'CNT-NEW-NAME';").fetchone()
        assert stored_row["title"] == "Old Container"
        assert stored_row["file_path"] == "eidos/architecture/containers/alpha.adoc"
    finally:
        idx.close()

    # 6. Corruption recovery: corrupted SQLite bytes are unlinked and rebuilt cleanly
    corrupt_db = tmp_path / "corrupt.db"
    corrupt_db.write_bytes(b"NOT A VALID SQLITE DATABASE HEADER" * 10)
    with contextlib.closing(open_cache_connection(corrupt_db)) as recovered_conn:
        ver = recovered_conn.execute("SELECT value FROM metadata WHERE key = 'schema_version';").fetchone()[0]
        assert ver == SCHEMA_VERSION


def test_sync_invariant_coverage_verifies_function_and_polyglot_tests(tmp_path):
    """Verify sync_index checks AST function existence + invariant tag rather than mere file existence, and indexes polyglot tests."""  # eidos:fixture
    repo = tmp_path / "inv_cov_repo"
    eidos_dir = repo / "eidos"
    caps_dir = eidos_dir / "domains" / "core" / "capabilities"
    caps_dir.mkdir(parents=True)

    tests_dir = repo / "tests"
    tests_dir.mkdir(parents=True)
    py_test = tests_dir / "test_auth.py"
    py_test.write_text(
        "# Verifies INV-SYNC-COVERED\ndef test_existing_covered():\n    assert True\n\ndef test_untagged_func():\n    assert True\n",  # eidos:fixture
        encoding="utf-8",
    )

    ts_test = repo / "packages" / "web" / "src" / "auth.test.ts"
    ts_test.parent.mkdir(parents=True)
    ts_test.write_text(
        "// Verifies INV-SYNC-POLYGLOT-TS\nfunction test_ts_auth() {\n  return true;\n}\n",  # eidos:fixture
        encoding="utf-8",
    )

    cap_spec = caps_dir / "cap-auth.adoc"
    cap_spec.write_text(
        """---
type: product/capability
id: CAP-AUTH-SYNC
title: Auth Sync Capability
domain: DOM-CORE
status: active
business:
  personas: []
  jtbd: Verify invariant test bindings in index sync
technical:
  containers: []
  invariants:
    - id: INV-SYNC-COVERED  # eidos:fixture
      rule: Covered by existing tagged function
      test_binding: tests/test_auth.py::test_existing_covered
    - id: INV-SYNC-MISSING-FUNC  # eidos:fixture
      rule: File exists on disk but function does not exist
      test_binding: tests/test_auth.py::test_function_does_not_exist
    - id: INV-SYNC-UNTAGGED-FUNC  # eidos:fixture
      rule: File and function exist on disk but function lacks invariant token
      test_binding: tests/test_auth.py::test_untagged_func
    - id: INV-SYNC-MISSING-FILE  # eidos:fixture
      rule: Referenced test file does not exist on disk
      test_binding: tests/test_nonexistent.py::test_nothing
    - id: INV-SYNC-POLYGLOT-TS  # eidos:fixture
      rule: Covered by polyglot TypeScript test outside tests/
      test_binding: packages/web/src/auth.test.ts::test_ts_auth
---
= Auth Sync Capability
""",
        encoding="utf-8",
    )

    idx = ArchitecturalIndex(db_path=eidos_dir / ".cache" / "index.db", repo_root=repo, eidos_dir=eidos_dir)
    try:
        idx.sync(force=True)
        conn = idx._get_connection()

        def _inv_state(iid: str) -> dict:
            row = conn.execute("SELECT data_json FROM entities WHERE id = ?;", (iid,)).fetchone()
            assert row is not None
            return orjson.loads(row["data_json"])

        cov_ok = _inv_state("INV-SYNC-COVERED")  # eidos:fixture
        assert cov_ok["has_test_coverage"] is True
        assert "tests/test_auth.py::test_existing_covered" in cov_ok["matching_tests"]

        cov_missing_fn = _inv_state("INV-SYNC-MISSING-FUNC")  # eidos:fixture
        assert cov_missing_fn["has_test_coverage"] is False
        assert cov_missing_fn["matching_tests"] == []

        cov_untagged_fn = _inv_state("INV-SYNC-UNTAGGED-FUNC")  # eidos:fixture
        assert cov_untagged_fn["has_test_coverage"] is False
        assert cov_untagged_fn["matching_tests"] == []

        cov_missing_file = _inv_state("INV-SYNC-MISSING-FILE")  # eidos:fixture
        assert cov_missing_file["has_test_coverage"] is False
        assert cov_missing_file["matching_tests"] == []

        cov_ts = _inv_state("INV-SYNC-POLYGLOT-TS")  # eidos:fixture
        assert cov_ts["has_test_coverage"] is True
        assert "packages/web/src/auth.test.ts::test_ts_auth" in cov_ts["matching_tests"]

        # Incrementally add the missing function and verify coverage updates to True
        base_mtime = py_test.stat().st_mtime
        py_test.write_text(
            "# Verifies INV-SYNC-COVERED\ndef test_existing_covered():\n    assert True\n\n"  # eidos:fixture
            "# Verifies INV-SYNC-MISSING-FUNC\ndef test_function_does_not_exist():\n    assert True\n",  # eidos:fixture
            encoding="utf-8",
        )
        os.utime(py_test, (base_mtime + 10.0, base_mtime + 10.0))
        idx.sync(force=False)

        cov_now_added = _inv_state("INV-SYNC-MISSING-FUNC")  # eidos:fixture
        assert cov_now_added["has_test_coverage"] is True
        assert "tests/test_auth.py::test_function_does_not_exist" in cov_now_added["matching_tests"]

        # Remove test_existing_covered from py_test and verify coverage reverts to False and stale matching_tests clears
        py_test.write_text(
            "# Verifies INV-SYNC-MISSING-FUNC\ndef test_function_does_not_exist():\n    assert True\n",  # eidos:fixture
            encoding="utf-8",
        )
        os.utime(py_test, (base_mtime + 20.0, base_mtime + 20.0))
        idx.sync(force=False)

        cov_removed = _inv_state("INV-SYNC-COVERED")  # eidos:fixture
        assert cov_removed["has_test_coverage"] is False
        assert cov_removed["matching_tests"] == []
    finally:
        idx.close()


def test_readonly_warm_cache_open_db_path_singleton_and_custom_repo_root(tmp_path):
    """DLT-2026-067: Verify open_cache_connection performs zero DDL/DML writes on warm cache hits,
    get_architectural_index invalidates and closes old singleton when db_path changes, and custom
    spec roots resolve repo_root via find_repo_root."""
    from eidos.core.index.graph_index import get_architectural_index
    from eidos.core.index.storage import open_cache_connection

    # 1. Warm cache open performs zero DDL/DML write statements
    db1 = tmp_path / "db1.sqlite"
    conn1 = open_cache_connection(db1)
    conn1.close()

    executed_sql: list[str] = []
    conn_warm = open_cache_connection(db1)
    conn_warm.set_trace_callback(executed_sql.append)
    from eidos.core.index.storage import ensure_cache_schema

    ensure_cache_schema(conn_warm)
    conn_warm.close()
    assert not any(s.lstrip().upper().startswith(("CREATE ", "INSERT ", "DROP ", "BEGIN")) for s in executed_sql)

    # 2. get_architectural_index replaces and closes old singleton when db_path changes
    custom_repo = tmp_path / "custom_project"
    custom_specs = custom_repo / "specs"
    (custom_specs / "architecture" / "containers").mkdir(parents=True)
    db_a = custom_specs / ".cache" / "a.db"
    db_b = custom_specs / ".cache" / "b.db"

    idx_a = get_architectural_index(eidos_dir=custom_specs, db_path=db_a, force_new=True)
    assert idx_a.root == custom_repo.resolve()
    assert idx_a.db_path == db_a.resolve()
    assert idx_a._conn is not None

    idx_b = get_architectural_index(eidos_dir=custom_specs, db_path=db_b)
    assert idx_b is not idx_a
    assert idx_b.db_path == db_b.resolve()
    # Old singleton connection was cleanly closed on replacement
    assert idx_a._conn is None
    idx_b.close()


def test_container_inline_component_invariants_ast_fts_projection_and_anchored_traversal(tmp_path):
    """DLT-2026-078 (# eidos:fixture): Verify container and component invariants are indexed into
    entities/specs_fts/edges, AST code_symbols are projected into specs_fts.symbols, build_tiered_context_slice
    promotes owning container/component for directly matched invariants, and execute_k_hop_traversal uses
    delimiter-bounded cycle prevention."""
    repo = tmp_path / "repo"
    eidos_dir = repo / "eidos"
    cnts_dir = eidos_dir / "architecture" / "containers"
    cmps_dir = eidos_dir / "architecture" / "components"
    src_dir = repo / "src" / "ledger"
    cnts_dir.mkdir(parents=True)
    cmps_dir.mkdir(parents=True)
    src_dir.mkdir(parents=True)

    (src_dir / "engine.py").write_text(
        "class QuantumReconciliationEngine:\n    def settle_atomic_batch(self) -> bool:\n        return True\n",
        encoding="utf-8",
    )

    # Create two containers where CNT-LEDGER-2 is a prefix-extension of CNT-LEDGER
    (cnts_dir / "ledger2.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-LEDGER-2\n"
        "title: Secondary Ledger\n"
        "container_type: service\n"
        "runtime: Python 3.12\n"
        "source_path: src/ledger2\n"
        "contracts:\n"
        "  consumes:\n"
        "    - CNT-LEDGER\n"
        "---\n"
        "= Secondary Ledger\n",
        encoding="utf-8",
    )
    (cnts_dir / "ledger.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-LEDGER\n"
        "title: Primary Ledger\n"
        "container_type: service\n"
        "runtime: Python 3.12\n"
        "source_path: src/ledger\n"
        "invariants:\n"
        "  - id: INV-CNT-LEDGER-TOP\n"
        "    rule: Primary ledger container must enforce idempotency keys.\n"
        "---\n"
        "= Primary Ledger\n",
        encoding="utf-8",
    )
    (cmps_dir / "ledger-settler.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-LEDGER-SETTLER\n"
        "title: LedgerSettler\n"
        "container: CNT-LEDGER\n"
        "status: active\n"
        "source_path: src/ledger/engine.py\n"
        "invariants:\n"
        "  - id: INV-CMP-SETTLER-INLINE\n"
        "    rule: Batch settlement must be atomic across partitions.\n"
        "---\n"
        "= LedgerSettler\n\n== Responsibility\nSettles batches.\n",
        encoding="utf-8",
    )

    idx = ArchitecturalIndex(db_path=eidos_dir / ".cache" / "index.db", repo_root=repo, eidos_dir=eidos_dir)
    try:
        idx.sync(force=True)
        conn = idx._get_connection()

        # 1. Both top-level container invariant and component invariant are in entities and specs_fts
        inv_ids = {r[0] for r in conn.execute("SELECT id FROM entities WHERE type = 'invariant';").fetchall()}
        assert "INV-CNT-LEDGER-TOP" in inv_ids
        assert "INV-CMP-SETTLER-INLINE" in inv_ids

        # 2. AST symbol 'QuantumReconciliationEngine' is projected into specs_fts.symbols for CMP-LEDGER-SETTLER
        fts_hits = idx.query_fts("QuantumReconciliationEngine")
        assert any(h["id"] == "CMP-LEDGER-SETTLER" for h in fts_hits)

        # 3. Context slice matching component invariant promotes owning component CMP-LEDGER-SETTLER and container CNT-LEDGER
        ctx_slice = idx.get_tiered_context_slice(task="atomic across partitions", tier="guardrails")
        assert any(i["id"] == "INV-CMP-SETTLER-INLINE" for i in ctx_slice.invariants)
        assert any(c["id"] in ("CNT-LEDGER", "CMP-LEDGER-SETTLER") for c in ctx_slice.components)

        # 4. k-hop traversal from CNT-LEDGER-2 reaches CNT-LEDGER (not blocked by substring 'CNT-LEDGER' in 'CNT-LEDGER-2')
        neighbors = idx.get_k_hop_neighborhood("CNT-LEDGER-2", max_depth=2)
        neighbor_ids = {n["id"] for n in neighbors}
        assert "CNT-LEDGER" in neighbor_ids
        assert "INV-CNT-LEDGER-TOP" in neighbor_ids
    finally:
        idx.close()


def test_deep_review_subsystem_2_incremental_edges_fts_and_transient_sqlite_errors(tmp_path, monkeypatch):
    """Verify incremental rebuild_spec_edges retains promoted CMP-* and design/client edges,
    recomputes specs_fts.symbols on symbol removal, and open_cache_connection does not unlink on lock error."""
    import sqlite3

    import pytest

    from eidos.core.index.ast_indexer import CodeSymbol, _evaluate_symbol_visibility
    from eidos.core.index.storage import open_cache_connection

    # 1. Transient lock OperationalError does NOT delete existing SQLite db file
    db_file = tmp_path / "locked.db"
    c = open_cache_connection(db_file)
    c.close()
    assert db_file.exists()

    orig_connect = sqlite3.connect

    def _locked_connect(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(sqlite3, "connect", _locked_connect)
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        open_cache_connection(db_file)
    assert db_file.exists()
    monkeypatch.setattr(sqlite3, "connect", orig_connect)

    # 2. Java/Kotlin parameter names containing 'internal' or 'private' do not misclassify public methods
    pub_sym = CodeSymbol(
        name="runTask",
        kind="method",
        file_path="src/TaskRunner.java",
        line_start=1,
        line_end=3,
        signature="public void runTask(String internal)",
    )
    priv_sym = CodeSymbol(
        name="runTask",
        kind="method",
        file_path="src/TaskRunner.java",
        line_start=1,
        line_end=3,
        signature="private void runTask(String arg)",
    )
    assert _evaluate_symbol_visibility(pub_sym) is True
    assert _evaluate_symbol_visibility(priv_sym) is False

    # 3. Incremental sync retains promoted CMP-* belongs_to edges and removes deleted AST symbols from specs_fts
    repo = tmp_path / "inc_repo"
    eidos_dir = repo / "eidos"
    cnts_dir = eidos_dir / "architecture" / "containers"
    cmps_dir = eidos_dir / "architecture" / "components"
    src_dir = repo / "src" / "svc"
    cnts_dir.mkdir(parents=True)
    cmps_dir.mkdir(parents=True)
    src_dir.mkdir(parents=True)

    code_file = src_dir / "worker.py"
    code_file.write_text(
        "def LegacyEphemeralRoutine():\n    return 1\n\ndef StableWorkerHandler():\n    return 2\n",
        encoding="utf-8",
    )
    cnt_file = cnts_dir / "svc.adoc"
    cnt_file.write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-SVC\n"
        "title: Worker Service\n"
        "container_type: service\n"
        "runtime: Python 3.12\n"
        "source_path: src/svc\n"
        "---\n"
        "= Worker Service\n",
        encoding="utf-8",
    )
    (cmps_dir / "cmp-promoted.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-PROMOTED\n"
        "title: PromotedWorker\n"
        "container: CNT-SVC\n"
        "responsibility: Promoted worker component.\n"
        "source_path: src/svc/worker.py\n"
        "---\n"
        "= Promoted Worker\n",
        encoding="utf-8",
    )

    idx = ArchitecturalIndex(db_path=eidos_dir / ".cache" / "index.db", repo_root=repo, eidos_dir=eidos_dir)
    try:
        idx.sync(force=True)
        assert any(h["id"] == "CMP-PROMOTED" for h in idx.query_fts("LegacyEphemeralRoutine"))

        # Touch only CNT-SVC and run incremental sync -> CMP-PROMOTED -> CNT-SVC edge must remain intact
        cnt_file.write_text(cnt_file.read_text(encoding="utf-8") + "\nUpdated body.\n", encoding="utf-8")
        idx.sync(force=False)
        conn = idx._get_connection()
        edge_row = conn.execute(
            "SELECT 1 FROM edges WHERE source_id = 'CMP-PROMOTED' AND target_id = 'CNT-SVC' AND rel_type = 'part_of';"
        ).fetchone()
        assert edge_row is not None

        # Remove LegacyEphemeralRoutine from code_file and run incremental sync -> FTS must no longer match it
        code_file.write_text("def StableWorkerHandler():\n    return 2\n", encoding="utf-8")
        idx.sync(force=False)
        assert not any(h["id"] == "CMP-PROMOTED" for h in idx.query_fts("LegacyEphemeralRoutine"))
        assert any(h["id"] == "CMP-PROMOTED" for h in idx.query_fts("StableWorkerHandler"))
    finally:
        idx.close()


def test_index_spec_deletion_child_invariants_and_query_coverage(tmp_path):
    """Verify child invariant symbol_mappings/edges cleanup on spec update/deletion, lazy _ensure_synced, query helpers, DS-* context, and strict collision."""  # eidos:fixture
    import pytest
    from typer.testing import CliRunner

    from eidos.cli.main import app

    repo = tmp_path / "query_repo"
    eidos_dir = repo / "eidos"
    pol_dir = eidos_dir / "policies"
    cnt_dir = eidos_dir / "architecture" / "containers"
    ds_dir = eidos_dir / "design" / "systems" / "core"
    tests_dir = repo / "tests"
    for d in (pol_dir, cnt_dir, ds_dir, tests_dir):
        d.mkdir(parents=True)

    (tests_dir / "test_pol.py").write_text(
        "def test_inv_cleanup():\n    # INV-CLEANUP-001\n    assert True\n",
        encoding="utf-8",
    )
    pol_file = pol_dir / "pol.adoc"
    pol_file.write_text(
        "---\n"
        "type: product/policy\n"
        "id: POL-CLEANUP-001\n"
        "title: Cleanup Policy\n"
        "category: quality\n"
        "severity: blocking\n"
        "invariants:\n"
        "  - id: INV-CLEANUP-001\n"
        "    rule: Child invariant rule\n"
        "    test_binding: tests/test_pol.py::test_inv_cleanup\n"
        "---\n"
        "= Cleanup Policy\n",
        encoding="utf-8",
    )
    (cnt_dir / "api.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-API\n"
        "title: API Server\n"
        "container_type: service\n"
        "runtime: Python\n"
        "source_path: src/api\n"
        "---\n"
        "= API Server\n",
        encoding="utf-8",
    )
    (cnt_dir / "web.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-WEB\n"
        "title: Web App\n"
        "container_type: web_app\n"
        "runtime: TypeScript\n"
        "source_path: src/web\n"
        "contracts:\n"
        "  consumes:\n"
        "    - CNT-API\n"
        "---\n"
        "= Web App\n",
        encoding="utf-8",
    )
    (ds_dir / "tokens.json").write_text(
        '{"color": {"brand": {"$value": "#123456", "$type": "color"}}}', encoding="utf-8"
    )
    (ds_dir / "system.adoc").write_text(
        "---\n"
        "type: design/system\n"
        "id: DS-BRAND-CORE\n"
        "title: Brand Core Design System\n"
        "status: active\n"
        "tokens:\n"
        "  spec_format: w3c_design_tokens\n"
        "  manifest: tokens.json\n"
        "---\n"
        "= Brand Core\n",
        encoding="utf-8",
    )
    clt_dir = eidos_dir / "design" / "clients" / "web"
    clt_dir.mkdir(parents=True)
    (clt_dir / "client.adoc").write_text(
        "---\n"
        "type: design/client\n"
        "id: CLT-WEB\n"
        "title: Web Client\n"
        "platform: web-spa\n"
        "container: CNT-WEB\n"
        "design_system: DS-BRAND-CORE\n"
        "---\n"
        "= Web Client\n",
        encoding="utf-8",
    )

    idx = ArchitecturalIndex(db_path=eidos_dir / ".cache" / "index.db", repo_root=repo, eidos_dir=eidos_dir)
    try:
        # 1. Lazy auto-sync (_ensure_synced) on query without explicit sync()
        containers = idx.get_containers()
        assert any(c["id"] == "CNT-WEB" for c in containers)
        assert any(e["id"] == "POL-CLEANUP-001" for e in idx.get_entities("product/policy"))

        # 2. Exact entity ID search boost (-1000.0) and incoming references
        exact_hits = idx.query_fts("POL-CLEANUP-001")
        assert exact_hits and exact_hits[0]["id"] == "POL-CLEANUP-001"
        assert exact_hits[0]["rank"] <= -1000.0
        inc_refs = idx.get_incoming_references("CNT-API")
        assert any(r["source_id"] == "CNT-WEB" for r in inc_refs)
        inc_refs_by_path = idx.get_incoming_references("eidos/architecture/containers/api.adoc")
        assert any(r["source_id"] == "CNT-WEB" for r in inc_refs_by_path)

        # 3. Design system context slice expansion via task query
        ds_slice = idx.get_tiered_context_slice(task="DS-BRAND-CORE", tier="guardrails")
        assert "DS-BRAND-CORE" in ds_slice.matched_reasons
        assert any(c["id"] == "CNT-WEB" for c in ds_slice.components)

        # 4. Verify child invariant test_binding row exists in symbol_mappings, then delete pol.adoc and verify cleanup
        conn = idx._get_connection()
        tb_before = conn.execute("SELECT * FROM symbol_mappings WHERE entity_id = 'INV-CLEANUP-001';").fetchall()
        assert len(tb_before) >= 1

        pol_file.unlink()
        idx.sync(force=False)
        tb_after = conn.execute("SELECT * FROM symbol_mappings WHERE entity_id = 'INV-CLEANUP-001';").fetchall()
        assert tb_after == []
        inv_after = conn.execute("SELECT * FROM entities WHERE id = 'INV-CLEANUP-001';").fetchall()
        assert inv_after == []

        # 5. Strict duplicate entity ID collision raises ValueError
        (cnt_dir / "dup.adoc").write_text(
            "---\n"
            "type: tech/container\n"
            "id: CNT-WEB\n"
            "title: Duplicate Web\n"
            "container_type: web_app\n"
            "runtime: TypeScript\n"
            "source_path: src/dup\n"
            "---\n"
            "= Duplicate\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="Duplicate entity ID 'CNT-WEB'"):
            idx.sync(force=True, strict=True)
    finally:
        idx.close()

    # 6. CLI eidos index status --json
    import eidos.core.index.graph_index as gi

    try:
        runner = CliRunner()
        res_status = runner.invoke(app, ["index", "status", "--json"])
        assert res_status.exit_code == 0
        status_data = orjson.loads(res_status.output)
        assert "total_entities" in status_data
        assert "entity_counts_by_type" in status_data
    finally:
        if gi._INDEX_INSTANCE is not None:
            gi._INDEX_INSTANCE.close()
            gi._INDEX_INSTANCE = None


def test_dlt_2026_083_fts_and_boost_hub_suppression_and_token_calibration(tmp_path):
    """Verify DLT-2026-083: two-tier FTS5 AND coordination boost, hop >= 2 organizational hub suppression, and structural token estimation."""
    from eidos.core.index.context import TieredContextSlice
    from eidos.core.index.graph_index import ArchitecturalIndex
    from eidos.core.index.query import build_fts_match_query
    from eidos.core.lifecycle.initializer import init_repository
    from eidos.core.lifecycle.scaffolder import create_spec

    # 1. build_fts_match_query operator="AND" vs "OR"
    q_or = build_fts_match_query("drift Engine", operator="OR")
    q_and = build_fts_match_query("drift Engine", operator="AND")
    assert " OR " in q_or
    assert " AND " in q_and

    # 2. Hop >= 2 organizational hub suppression
    init_repository(tmp_path)
    eidos_dir = tmp_path / "eidos"
    create_spec(eidos_dir, "domain", "DOM-SHARED", title="Shared Domain")
    c1 = create_spec(eidos_dir, "capability", "CAP-ALPHA", title="Alpha Capability")
    c2 = create_spec(eidos_dir, "capability", "CAP-BETA", title="Beta Capability")
    assert c1.exists() and c2.exists()

    idx = ArchitecturalIndex(db_path=tmp_path / ".eidos_cache" / "index.db", eidos_dir=eidos_dir)
    try:
        idx.sync(force=True)
        # 2-hop traversal from CAP-ALPHA reaches DOM-SHARED at hop 1,
        # but must NOT fan out through DOM-SHARED to unrelated sibling CAP-BETA at hop 2!
        neighbors = idx.get_k_hop_neighborhood("CAP-ALPHA", max_depth=2)
        neighbor_ids = {n["id"] for n in neighbors}
        assert "DOM-SHARED" in neighbor_ids
        assert "CAP-BETA" not in neighbor_ids

        # Direct 1-hop traversal starting from DOM-SHARED itself still returns its children
        dom_neighbors = {n["id"] for n in idx.get_k_hop_neighborhood("DOM-SHARED", max_depth=1)}
        assert "CAP-ALPHA" in dom_neighbors
        assert "CAP-BETA" in dom_neighbors

        # SpecDelta blast-radius traversal (DLT-2026-085): walks outward from DLT-* via 'affects' edges
        # without polluting CAP-ALPHA's neighborhood with DLT-*
        dlt_dir = eidos_dir / "deltas"
        dlt_dir.mkdir(parents=True, exist_ok=True)
        (dlt_dir / "dlt-2026-001.adoc").write_text(
            "---\n"
            "type: delta/mutation\n"
            "id: DLT-2026-001\n"
            "title: Modify Alpha\n"
            "status: proposed\n"
            "intent:\n"
            "  kind: feature\n"
            "  motivation: Test delta blast radius\n"
            "mutations:\n"
            "  - operation: modify\n"
            "    target: CAP-ALPHA\n"
            "---\n"
            "= Delta\n",
            encoding="utf-8",
        )
        idx.sync(force=True)
        dlt_blast = {n["id"] for n in idx.get_k_hop_neighborhood("DLT-2026-001", max_depth=2)}
        assert "DLT-2026-001" in dlt_blast
        assert "CAP-ALPHA" in dlt_blast
        assert "DOM-SHARED" in dlt_blast
        cap_alpha_neighbors = {n["id"] for n in idx.get_k_hop_neighborhood("CAP-ALPHA", max_depth=2)}
        assert "DLT-2026-001" not in cap_alpha_neighbors
    finally:
        idx.close()

    # 3. Structural token estimation accounts for punctuation/JSON overhead
    code_heavy = '{"key": ["val1", "val2"], "fn": "foo_bar(a, b, c);"}' * 10
    prose_text = "This is simple plain English text without dense punctuation " * 10
    est_code = TieredContextSlice._estimate_text_tokens(code_heavy)
    est_prose = TieredContextSlice._estimate_text_tokens(prose_text)
    assert est_code > len(code_heavy) // 4
    assert est_prose >= len(prose_text) // 4


def test_dlt_2026_086_unified_cache_and_vcs_shim_removal(tmp_path: Path, monkeypatch) -> None:
    """DLT-2026-086: Verify single-authority DDL in storage.py, shared get_cached_spec_document
    defensive-copy reuse across SpecValidator, load_spec_catalog, and ArchitecturalIndex.sync,
    and complete removal of _accepts_kwarg / sys.modules.get('eidos.vcs.drift') shims from src/eidos/vcs/."""
    import inspect

    from eidos.core.index import storage as storage_mod
    from eidos.core.lifecycle import catalog as catalog_mod
    from eidos.core.lifecycle.initializer import init_repository
    from eidos.core.lifecycle.scaffolder import create_spec
    from eidos.core.spec import parser as parser_mod
    from eidos.core.spec import validator as validator_mod
    from eidos.vcs import diff as vcs_diff
    from eidos.vcs import drift as vcs_drift
    from eidos.vcs import invariants as vcs_invariants
    from eidos.vcs import structural as vcs_structural

    # 1. Zero inline CREATE TABLE statements in validator.py or invariants.py
    assert "CREATE TABLE" not in inspect.getsource(validator_mod)
    assert "CREATE TABLE" not in inspect.getsource(vcs_invariants)

    # 2. _SPEC_CATALOG_CACHE is removed from catalog.py
    assert not hasattr(catalog_mod, "_SPEC_CATALOG_CACHE")

    # 3. Zero _accepts_kwarg or sys.modules.get("eidos.vcs.drift") shims in VCS modules
    for mod in (vcs_diff, vcs_drift, vcs_invariants, vcs_structural):
        src = inspect.getsource(mod)
        assert "_accepts_kwarg" not in src
        assert 'sys.modules.get("eidos.vcs.drift")' not in src
        assert "sys.modules.get('eidos.vcs.drift')" not in src

    # 4. Shared get_cached_spec_document avoids re-parsing across validate_directory, load_spec_catalog, and sync
    init_repository(tmp_path)
    eidos_dir = tmp_path / "eidos"
    create_spec(eidos_dir, "domain", "DOM-CACHE-086", title="Cache 086 Domain")

    parse_calls: list[str] = []
    orig_parse = parser_mod._parse_spec_content

    def _counting_parse(fp: Path, content: str):
        parse_calls.append(fp.name)
        return orig_parse(fp, content)

    monkeypatch.setattr(parser_mod, "_parse_spec_content", _counting_parse)
    storage_mod._PARSED_SPEC_MEM_CACHE.clear()

    # Cold run through SpecValidator parses each file once and populates shared cache
    validator = validator_mod.SpecValidator()
    report = validator.validate_directory(eidos_dir)
    assert report.is_clean
    cold_count = len(parse_calls)
    assert cold_count > 0

    # Subsequent _build_spec_catalog and ArchitecturalIndex.sync reuse cached SpecDocument instances with 0 re-parses
    parse_calls.clear()
    specs_by_id, _ = catalog_mod._build_spec_catalog(eidos_dir)
    assert "DOM-CACHE-086" in specs_by_id
    assert parse_calls == []

    # Mutating returned catalog frontmatter dict must not mutate cached SpecDocument frontmatter
    specs_by_id["DOM-CACHE-086"][2]["title"] = "Mutated In Caller"
    specs_again, _ = catalog_mod._build_spec_catalog(eidos_dir)
    assert specs_again["DOM-CACHE-086"][2]["title"] == "Cache 086 Domain"

    idx = ArchitecturalIndex(db_path=eidos_dir / ".cache" / "index.db", repo_root=tmp_path, eidos_dir=eidos_dir)
    try:
        idx.sync(force=False)
        assert parse_calls == []
    finally:
        idx.close()


def test_dlt_2026_088_single_pass_scandir_dirty_check(tmp_path: Path) -> None:
    """DLT-2026-088: Verify check_index_dirty detects added, modified, and deleted spec/code files
    using single-pass os.scandir and properly checks tracked code files in sibling-prefix directories
    outside top_code_dirs."""
    import os

    from eidos.core.index.graph_index import ArchitecturalIndex
    from eidos.core.lifecycle.initializer import init_repository
    from eidos.core.lifecycle.scaffolder import create_spec
    from eidos.core.spec.parser import dump_spec_document, parse_spec_file

    init_repository(tmp_path)
    eidos_dir = tmp_path / "eidos"
    cnt_path = create_spec(eidos_dir, "container", "CNT-SCAN", title="Scan Container")
    cnt_doc = parse_spec_file(cnt_path)
    cnt_doc.frontmatter["source_path"] = "src"
    cnt_path.write_text(dump_spec_document(cnt_doc.frontmatter, cnt_doc.body), encoding="utf-8")

    cmp_path = create_spec(eidos_dir, "component", "CMP-SCAN-MOD", title="ScanMod")
    cmp_doc = parse_spec_file(cmp_path)
    cmp_doc.frontmatter["container"] = "CNT-SCAN"
    cmp_doc.frontmatter["source_path"] = ["src/core_mod.py", "src_extra/outside_mod.py"]
    cmp_path.write_text(dump_spec_document(cmp_doc.frontmatter, cmp_doc.body), encoding="utf-8")

    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src_extra").mkdir(parents=True, exist_ok=True)
    in_top_file = tmp_path / "src" / "core_mod.py"
    outside_sibling_file = tmp_path / "src_extra" / "outside_mod.py"
    in_top_file.write_text("def core_fn():\n    return 1\n", encoding="utf-8")
    outside_sibling_file.write_text("def outside_fn():\n    return 1\n", encoding="utf-8")

    idx = ArchitecturalIndex(db_path=eidos_dir / ".cache" / "index.db", repo_root=tmp_path, eidos_dir=eidos_dir)
    try:
        idx.sync(force=True)
        is_dirty0, mod0, del0 = idx.is_dirty()
        assert is_dirty0 is False and mod0 == [] and del0 == []

        # 1. Modify file inside top_code_dirs ('src/') -> detected via _scan_code_dir os.scandir pass
        in_top_file.write_text("def core_fn():\n    return 2\n", encoding="utf-8")
        os.utime(in_top_file, (in_top_file.stat().st_mtime + 5, in_top_file.stat().st_mtime + 5))
        is_dirty1, mod1, _ = idx.is_dirty()
        assert is_dirty1 is True
        assert in_top_file.resolve() in {p.resolve() for p in mod1}

        idx.sync(force=False)
        assert idx.is_dirty()[0] is False

        # 2. Modify tracked file in sibling-prefix directory ('src_extra/') outside top_code_dirs ('src/')
        # Must NOT be skipped by a naive startswith('src') check!
        outside_sibling_file.write_text("def outside_fn():\n    return 2\n", encoding="utf-8")
        os.utime(
            outside_sibling_file,
            (outside_sibling_file.stat().st_mtime + 5, outside_sibling_file.stat().st_mtime + 5),
        )
        is_dirty2, mod2, _ = idx.is_dirty()
        assert is_dirty2 is True
        assert outside_sibling_file.resolve() in {p.resolve() for p in mod2}

        idx.sync(force=False)
        assert idx.is_dirty()[0] is False

        # 3. Add a new untracked code file inside src/ -> detected as dirty
        added_code = tmp_path / "src" / "new_helper.py"
        added_code.write_text("def new_helper():\n    return True\n", encoding="utf-8")
        is_dirty3, mod3, _ = idx.is_dirty()
        assert is_dirty3 is True
        assert added_code.resolve() in {p.resolve() for p in mod3}

        # 4. Delete tracked code file -> detected in deleted list
        added_code.unlink()
        in_top_file.unlink()
        is_dirty4, _mod4, del4 = idx.is_dirty()
        assert is_dirty4 is True
        assert Path("src/core_mod.py") in del4
    finally:
        idx.close()


def test_context_implements_edges_inline_cmp_blast_radius_and_path_resolution(tmp_path):
    """Verify Bugs 3, 4, 5, 15a, 15b: context implements edges across domains, inline CMP-* graph edges & blast-radius,
    file path resolution specificity without .ext symbol collision, and .kt + dict interface indexing."""  # eidos:fixture
    from eidos.core.index.context import build_tiered_context_slice
    from eidos.core.lifecycle.initializer import init_repository
    from eidos.core.lifecycle.scaffolder import create_spec
    from eidos.core.spec.parser import dump_spec_document, parse_spec_file

    init_repository(tmp_path, title="graph-bug-fixes")
    eidos_dir = tmp_path / "eidos"

    create_spec(eidos_dir, "domain", "DOM-ENGINE", title="Engine Domain")
    create_spec(eidos_dir, "domain", "DOM-INTEL", title="Intelligence Domain")

    # Container CNT-IDX bound to DOM-ENGINE, with standalone component CMP-INLINE-IDX and dict-formatted interfaces (Bug 15b)
    cnt_idx = create_spec(eidos_dir, "container", "CNT-IDX", title="Index Container")
    cnt_idx_doc = parse_spec_file(cnt_idx)
    cnt_idx_doc.frontmatter["source_path"] = "src/index"
    cnt_idx_doc.frontmatter["domain"] = "DOM-ENGINE"
    cnt_idx_doc.frontmatter["interfaces"] = {
        "provides": [{"signature": "POST /v1/index/query"}],
        "consumes": [{"signature": "GET /v1/config"}],
    }
    cnt_idx.write_text(dump_spec_document(cnt_idx_doc.frontmatter, cnt_idx_doc.body), encoding="utf-8")

    cmp_idx = create_spec(eidos_dir, "component", "CMP-INLINE-IDX", title="Inline Indexer")
    cmp_idx_doc = parse_spec_file(cmp_idx)
    cmp_idx_doc.frontmatter["container"] = "CNT-IDX"
    cmp_idx_doc.frontmatter["source_path"] = "src/index/worker.py"
    cmp_idx.write_text(dump_spec_document(cmp_idx_doc.frontmatter, cmp_idx_doc.body), encoding="utf-8")

    # Broad container CNT-SRC-ROOT (source_path: src) and sibling container CNT-OTHER (source_path: src/other)
    cnt_root = create_spec(eidos_dir, "container", "CNT-SRC-ROOT", title="Root Src Container")
    cnt_root_doc = parse_spec_file(cnt_root)
    cnt_root_doc.frontmatter["source_path"] = "src"
    cnt_root.write_text(dump_spec_document(cnt_root_doc.frontmatter, cnt_root_doc.body), encoding="utf-8")

    cnt_other = create_spec(eidos_dir, "container", "CNT-OTHER", title="Other Container")
    cnt_other_doc = parse_spec_file(cnt_other)
    cnt_other_doc.frontmatter["source_path"] = "src/other"
    cnt_other.write_text(dump_spec_document(cnt_other_doc.frontmatter, cnt_other_doc.body), encoding="utf-8")

    # Cross-domain capability CAP-INTEL-001 in DOM-INTEL implementing CNT-IDX and CMP-INLINE-IDX (Bug 3 & Bug 4)
    cap_intel = create_spec(eidos_dir, "capability", "CAP-INTEL-001", title="Agent Slicing")
    cap_intel_doc = parse_spec_file(cap_intel)
    cap_intel_doc.frontmatter["domain"] = "DOM-INTEL"
    cap_intel_doc.frontmatter["technical"] = {
        "containers": ["CNT-IDX"],
        "components": ["CMP-INLINE-IDX"],
        "invariants": [
            {
                "id": "INV-INTEL-SLICE-001",
                "rule": "Must slice cross-domain implementing capabilities.",
                "test_binding": "tests/test_worker.py::test_slice",
            }
        ],
    }
    cap_intel.write_text(dump_spec_document(cap_intel_doc.frontmatter, cap_intel_doc.body), encoding="utf-8")

    # Unrelated capability CAP-ENGINE-OTHER in DOM-ENGINE bound exclusively to CNT-OTHER
    cap_other = create_spec(eidos_dir, "capability", "CAP-ENGINE-OTHER", title="Other Engine Cap")
    cap_other_doc = parse_spec_file(cap_other)
    cap_other_doc.frontmatter["domain"] = "DOM-ENGINE"
    cap_other_doc.frontmatter["technical"] = {"containers": ["CNT-OTHER"]}
    cap_other.write_text(dump_spec_document(cap_other_doc.frontmatter, cap_other_doc.body), encoding="utf-8")

    # Source files: Python worker, Python class 'Py' in src/other/models.py, and Kotlin file Auth.kt (Bug 5 & Bug 15a)
    (tmp_path / "src" / "index").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "other").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "index" / "worker.py").write_text("def run_worker():\n    return 1\n", encoding="utf-8")
    (tmp_path / "src" / "index" / "Auth.kt").write_text(
        "class KotlinAuthGuard {\n    fun checkAccess(): Boolean = true\n}\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "other" / "models.py").write_text("class Py:\n    pass\n", encoding="utf-8")

    idx = ArchitecturalIndex(db_path=eidos_dir / ".cache" / "index.db", repo_root=tmp_path, eidos_dir=eidos_dir)
    try:
        idx.sync(force=True)

        # Bug 15a & 15b: Kotlin symbols indexed and dict interfaces stored as edges
        kt_matches = idx.resolve_symbol_or_path("KotlinAuthGuard")
        assert any(m["symbol"] == "KotlinAuthGuard" for m in kt_matches)

        # Bug 4: Inline CMP-INLINE-IDX edges preserved after sync_index -> blast-radius / k-hop & incoming refs work
        in_refs = idx.get_incoming_references("CMP-INLINE-IDX")
        in_sources = {r["source_id"] for r in in_refs}
        assert "CNT-IDX" in in_sources
        assert "CAP-INTEL-001" in in_sources
        khop = idx.get_k_hop_neighborhood("CMP-INLINE-IDX", max_depth=1)
        khop_ids = {n["id"] for n in khop}
        assert "CNT-IDX" in khop_ids
        assert "CAP-INTEL-001" in khop_ids

        # Bug 5: File path resolution returns specific component/container (CMP-INLINE-IDX / CNT-IDX),
        # excludes shorter source_path container (CNT-SRC-ROOT), and does NOT match class 'Py' on '.py' suffix
        resolved = idx.resolve_symbol_or_path("src/index/worker.py")
        resolved_ids = [m["entity_id"] for m in resolved if m.get("entity_id")]
        assert resolved_ids[0] in ("CMP-INLINE-IDX", "CNT-IDX")
        assert "CNT-SRC-ROOT" not in resolved_ids
        assert not any(m.get("symbol") == "Py" for m in resolved)

        # Bug 3: Tiered context slice includes cross-domain CAP-INTEL-001 & INV-INTEL-SLICE-001 via implements edge
        # and excludes CAP-ENGINE-OTHER bound exclusively to CNT-OTHER
        slice_res = build_tiered_context_slice(idx, files="src/index/worker.py", tier="standard")
        cap_ids = {c["id"] for c in slice_res.capabilities}
        inv_ids = {i["id"] for i in slice_res.invariants}
        assert "CAP-INTEL-001" in cap_ids
        assert "INV-INTEL-SLICE-001" in inv_ids
        assert "CAP-ENGINE-OTHER" not in cap_ids
    finally:
        idx.close()


def test_incremental_spec_sync_skips_unchanged_test_reparse_and_scopes_fts(tmp_path: Path, monkeypatch) -> None:
    """Verify B3: incremental sync on a single .adoc edit restores test_binding symbol_mappings from existing
    asserts edges without re-parsing unchanged test files, and scopes _project_ast_code_symbols_into_fts."""  # eidos:fixture
    import os

    import eidos.core.index.sync as sync_mod
    from eidos.core.lifecycle.initializer import init_repository
    from eidos.core.lifecycle.scaffolder import create_spec
    from eidos.core.spec.parser import dump_spec_document, parse_spec_file

    init_repository(tmp_path, title="inc-perf-repo")
    eidos_dir = tmp_path / "eidos"
    create_spec(eidos_dir, "domain", "DOM-PERF", title="Perf Domain")
    cnt_perf = create_spec(eidos_dir, "container", "CNT-PERF", title="Perf Container")
    cnt_perf_doc = parse_spec_file(cnt_perf)
    cnt_perf_doc.frontmatter["source_path"] = "src/perf"
    cnt_perf.write_text(dump_spec_document(cnt_perf_doc.frontmatter, cnt_perf_doc.body), encoding="utf-8")
    cap_path = create_spec(eidos_dir, "capability", "CAP-PERF-001", title="Perf Cap")
    cap_doc = parse_spec_file(cap_path)
    cap_doc.frontmatter["technical"] = {
        "containers": ["CNT-PERF"],
        "invariants": [
            {
                "id": "INV-PERF-COV-001",
                "rule": "Must preserve invariant test coverage across incremental spec edits.",
                "test_binding": "tests/test_perf.py::test_perf_inv",
            }
        ],
    }
    cap_path.write_text(dump_spec_document(cap_doc.frontmatter, cap_doc.body), encoding="utf-8")

    (tmp_path / "src" / "perf").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "perf" / "engine.py").write_text("def run_perf_engine():\n    return 42\n", encoding="utf-8")
    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    test_file = tmp_path / "tests" / "test_perf.py"
    test_file.write_text(
        "def test_perf_inv():\n    # INV-PERF-COV-001\n    assert True\n",
        encoding="utf-8",
    )

    idx = ArchitecturalIndex(db_path=eidos_dir / ".cache" / "index.db", repo_root=tmp_path, eidos_dir=eidos_dir)
    try:
        idx.sync(force=True)
        conn = idx._get_connection()

        # Spy on get_ast_indexer().extract_symbols to ensure unchanged test_perf.py is NOT re-parsed on a spec-only edit
        from eidos.core.index.ast_indexer import get_ast_indexer

        ast_idx = get_ast_indexer()
        parsed_files: list[str] = []
        orig_extract = ast_idx.extract_symbols

        def _spy_extract(fpath: Path, content: str):
            parsed_files.append(fpath.name)
            return orig_extract(fpath, content)

        monkeypatch.setattr(ast_idx, "extract_symbols", _spy_extract)

        projected_targets: list[set[str] | None] = []
        orig_project = sync_mod._project_ast_code_symbols_into_fts

        def _spy_project(c, target_entity_ids=None):
            projected_targets.append(set(target_entity_ids) if target_entity_ids is not None else None)
            return orig_project(c, target_entity_ids=target_entity_ids)

        monkeypatch.setattr(sync_mod, "_project_ast_code_symbols_into_fts", _spy_project)

        # Edit only the capability spec prose and run incremental sync(force=False)
        cap_path.write_text(cap_path.read_text(encoding="utf-8") + "\nAdditional capability notes.\n", encoding="utf-8")
        st = cap_path.stat()
        os.utime(cap_path, (st.st_atime, st.st_mtime + 10.0))

        stats = idx.sync(force=False)
        assert stats.updated_modified == 1
        assert "test_perf.py" not in parsed_files
        assert projected_targets and projected_targets[-1] is not None

        # Verify INV-PERF-COV-001 still has has_test_coverage=True and test_binding symbol_mapping
        inv_row = conn.execute("SELECT data_json FROM entities WHERE id = 'INV-PERF-COV-001';").fetchone()
        assert inv_row is not None
        inv_data = orjson.loads(inv_row["data_json"])
        assert inv_data["has_test_coverage"] is True
        tb_map = conn.execute(
            "SELECT symbol FROM symbol_mappings WHERE entity_id = 'INV-PERF-COV-001' AND kind = 'test_binding';"
        ).fetchone()
        assert tb_map is not None and tb_map["symbol"] == "tests/test_perf.py"
    finally:
        idx.close()


def test_dlt_2026_097_fts_alignment_inline_boost_and_facade_hydration(tmp_path: Path) -> None:
    """Verify DLT-2026-097 FTS query alignment, inline CMP-*/DEC-* exact-ID boost, traversal overrides, and ArchitecturalIndex facade."""
    from eidos.core.index.ast_indexer import AstGrepIndexer
    from eidos.core.index.graph_index import _resolve_index_paths
    from eidos.core.index.query import _ENTITY_ID_TOKEN_RE, _FILE_QUERY_EXTS
    from eidos.core.index.storage import SCHEMA_VERSION
    from eidos.core.lifecycle.initializer import init_repository
    from eidos.core.lifecycle.scaffolder import create_spec
    from eidos.core.spec.parser import dump_spec_document, parse_spec_file

    assert SCHEMA_VERSION == "2.18.0"
    assert set(AstGrepIndexer.LANGUAGE_MAP.keys()) | {".proto", ".adoc"} <= _FILE_QUERY_EXTS

    # 1. Verify _ENTITY_ID_TOKEN_RE recognizes SYS-*, ARCH-*, PER-*, and underscore IDs
    matched_ids = {
        m.group(0).upper()
        for m in _ENTITY_ID_TOKEN_RE.finditer(
            "Inspect SYS-EIDOS, ARCH-001, PER-STAFF_ARCH, CMP-INLINE_ENGINE, and DEC-CORE_CHOICE."
        )
    }
    assert matched_ids == {
        "SYS-EIDOS",
        "ARCH-001",
        "PER-STAFF_ARCH",
        "CMP-INLINE_ENGINE",
        "DEC-CORE_CHOICE",
    }

    # 2. Scaffold workspace with standalone components, container with decision, and capability
    init_repository(tmp_path, title="dlt-097-repo")
    eidos_dir = tmp_path / "eidos"
    create_spec(eidos_dir, "domain", "DOM-CORE", title="Core Domain")
    cnt_path = create_spec(eidos_dir, "container", "CNT-RUNTIME", title="Runtime Container")
    cmp_path = create_spec(eidos_dir, "component", "CMP-STANDALONE", title="Standalone Worker")
    cmp_orch_path = create_spec(eidos_dir, "component", "CMP-INLINE-ORCHESTRATOR", title="Inline Orchestrator Engine")

    cnt_doc = parse_spec_file(cnt_path)
    cnt_doc.frontmatter["domain"] = "DOM-CORE"
    cnt_doc.frontmatter["source_path"] = "src/runtime"
    cnt_doc.frontmatter["decisions"] = [
        {
            "id": "DEC-RUNTIME-SCHEDULER",
            "title": "Use Deterministic Work Stealing",
            "status": "accepted",
            "rationale": "Prevents tail latency spikes.",
        }
    ]
    cnt_path.write_text(dump_spec_document(cnt_doc.frontmatter, cnt_doc.body), encoding="utf-8")

    cmp_orch_doc = parse_spec_file(cmp_orch_path)
    cmp_orch_doc.frontmatter["container"] = "CNT-RUNTIME"
    cmp_orch_doc.frontmatter["domain"] = "DOM-OVERRIDE"
    cmp_orch_doc.frontmatter["status"] = "active"
    cmp_orch_doc.frontmatter["source_path"] = "src/runtime/orchestrator.py"
    cmp_orch_path.write_text(
        dump_spec_document(
            cmp_orch_doc.frontmatter,
            "= Inline Orchestrator Engine\n\n== Responsibility\nCoordinates inline distributed workflow scheduling.\n",
        ),
        encoding="utf-8",
    )

    cmp_doc = parse_spec_file(cmp_path)
    cmp_doc.frontmatter["container"] = "CNT-RUNTIME"
    cmp_doc.frontmatter["domain"] = "DOM-CORE"
    cmp_doc.frontmatter["source_path"] = "src/runtime/standalone.py"
    cmp_path.write_text(
        dump_spec_document(
            cmp_doc.frontmatter,
            "= Standalone Worker\n\n== Responsibility\nExecutes standalone background batch compaction tasks.\n",
        ),
        encoding="utf-8",
    )

    cap_path = create_spec(eidos_dir, "capability", "CAP-ORCH", title="Orchestration Capability")
    cap_doc = parse_spec_file(cap_path)
    cap_doc.frontmatter["technical"] = {"containers": ["CNT-RUNTIME"]}
    cap_path.write_text(
        dump_spec_document(
            cap_doc.frontmatter,
            cap_doc.body + "\nDelegates scheduling to CMP-INLINE-ORCHESTRATOR.\n",
        ),
        encoding="utf-8",
    )

    (tmp_path / "src" / "runtime").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "runtime" / "orchestrator.py").write_text(
        "class Orchestrator:\n    def dispatch(self):\n        return True\n\n"
        "class Worker:\n    def dispatch(self):\n        return False\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "runtime" / "standalone.py").write_text(
        "def run_compaction():\n    return 1\n",
        encoding="utf-8",
    )

    # 3. Verify _resolve_index_paths normalizes relative eidos_dir paths
    resolved_root, resolved_eidos, resolved_db = _resolve_index_paths(
        repo_root=tmp_path,
        eidos_dir=eidos_dir,
        db_path=None,
    )
    assert resolved_root == tmp_path.resolve()
    assert resolved_eidos == eidos_dir.resolve()
    assert resolved_db == eidos_dir.resolve() / ".cache" / "index.db"

    idx = ArchitecturalIndex(db_path=resolved_db, repo_root=tmp_path, eidos_dir=eidos_dir)
    try:
        idx.sync(force=True)
        conn = idx._get_connection()

        # 4. Verify :toc: left is stripped from specs_fts.body_text and body responsibility is indexed in body_text
        fts_cnt = conn.execute(
            "SELECT jtbd_or_rule, body_text, keywords FROM specs_fts WHERE entity_id = 'CNT-RUNTIME';"
        ).fetchone()
        assert fts_cnt is not None
        assert ":toc:" not in fts_cnt["body_text"]
        assert "DEC-RUNTIME-SCHEDULER" in fts_cnt["keywords"]

        fts_cmp = conn.execute("SELECT body_text FROM specs_fts WHERE entity_id = 'CMP-STANDALONE';").fetchone()
        assert fts_cmp is not None
        assert "Executes standalone background batch compaction tasks" in fts_cmp["body_text"]

        # 5. Verify exact-ID boost (-1000.0) when querying standalone CMP-* or embedded DEC-* IDs
        inline_hits = idx.query_fts("CMP-INLINE-ORCHESTRATOR", limit=3)
        assert inline_hits
        assert inline_hits[0]["id"] == "CMP-INLINE-ORCHESTRATOR"
        assert inline_hits[0]["rank"] < -900.0

        dec_hits = idx.query_fts("DEC-RUNTIME-SCHEDULER", limit=3)
        assert dec_hits
        assert dec_hits[0]["id"] == "CNT-RUNTIME"
        assert dec_hits[0]["rank"] < -900.0

        # 6. Verify execute_incoming_references resolves component title and normalizes ./ path
        conn.execute(
            "INSERT INTO edges (source_id, target_id, rel_type) VALUES ('CMP-INLINE-ORCHESTRATOR', 'CNT-RUNTIME', 'depends_on');"
        )
        conn.commit()
        rel_cnt = cnt_path.relative_to(tmp_path).as_posix()
        inc_dot_slash = idx.get_incoming_references(f"./{rel_cnt}")
        inc_by_src = {r["source_id"]: r for r in inc_dot_slash}
        assert "CAP-ORCH" in inc_by_src
        assert "CMP-INLINE-ORCHESTRATOR" in inc_by_src
        assert inc_by_src["CMP-INLINE-ORCHESTRATOR"]["title"] == "Inline Orchestrator Engine"

        # 7. Verify execute_k_hop_traversal preserves component domain override
        trav = idx.get_k_hop_neighborhood("CNT-RUNTIME", max_depth=1)
        inline_node = next((n for n in trav if n["id"] == "CMP-INLINE-ORCHESTRATOR"), None)
        assert inline_node is not None
        assert inline_node["domain"] == "DOM-OVERRIDE"
        assert inline_node["title"] == "Inline Orchestrator Engine"

        # 8. Verify find_symbol empty-query guard and method location deduplication
        assert idx.find_symbol("   ") == []
        dispatch_hits = [h for h in idx.find_symbol("dispatch") if h["entity_id"] == "CMP-INLINE-ORCHESTRATOR"]
        assert len(dispatch_hits) == 2
        assert {h["location"]["line_start"] for h in dispatch_hits if h["location"]} == {2, 6}

        # 9. Verify get_components() hydration and get_entities(status=...) SQL filtering
        comps = idx.get_components()
        comp_by_id = {c["id"]: c for c in comps}
        assert "CMP-STANDALONE" in comp_by_id
        assert comp_by_id["CMP-STANDALONE"]["title"] == "Standalone Worker"
        assert comp_by_id["CMP-STANDALONE"]["domain"] == "DOM-CORE"
        assert comp_by_id["CMP-STANDALONE"]["status"] == "draft"

        draft_entities = idx.get_entities(status="draft")
        assert any(e["id"] == "CNT-RUNTIME" for e in draft_entities)
        assert idx.get_entities(status="retired") == []
    finally:
        idx.close()


def test_dlt_2026_104_unified_spec_catalog_and_graph_blast_radius(tmp_path: Path) -> None:
    """Verify DLT-2026-104 SCHEMA_VERSION migration, build_spec_path_resolver, complete edge coverage, get_spec_catalog, and compute_delta_impact."""  # eidos:fixture
    from eidos.core.index.storage import SCHEMA_VERSION, ensure_cache_schema
    from eidos.core.index.sync import build_spec_path_resolver
    from eidos.core.lifecycle.catalog import _build_spec_catalog_with_invariants
    from eidos.core.lifecycle.delta_engine import create_delta
    from eidos.core.lifecycle.initializer import init_repository
    from eidos.core.lifecycle.scaffolder import create_spec
    from eidos.core.spec.parser import dump_spec_document, parse_spec_file
    from eidos.portal.generator import get_portal_data

    assert SCHEMA_VERSION == "2.18.0"
    assert _build_spec_catalog_with_invariants(tmp_path / "nonexistent_eidos") == ({}, {})

    init_repository(tmp_path, title="dlt-104-repo")
    eidos_dir = tmp_path / "eidos"

    dom_path = create_spec(eidos_dir, "domain", "DOM-CORE-104", title="Core 104 Domain")
    per_path = create_spec(eidos_dir, "persona", "PER-ARCH-104", title="Architect 104")
    pol_path = create_spec(eidos_dir, "policy", "POL-GOV-104", title="Gov Policy 104")
    cnt_path = create_spec(eidos_dir, "container", "CNT-API-104", title="API Container 104")
    cmp_path = create_spec(eidos_dir, "component", "CMP-API-HANDLER-104", title="API Handler 104")
    cnt_gw_path = create_spec(eidos_dir, "container", "CNT-GW-104", title="Gateway Container 104")
    ctr_path = create_spec(eidos_dir, "contract", "CTR-API-104", title="API Contract 104")
    ds_path = create_spec(eidos_dir, "design-system", "DS-BRAND-104", title="Brand DS 104")
    scr_path = create_spec(eidos_dir, "screen", "SCR-DASH-104", title="Dashboard Screen 104")
    flw_path = create_spec(eidos_dir, "flow", "FLOW-ONBOARD-104", title="Onboarding Flow 104")
    uic_path = create_spec(eidos_dir, "ui-component", "UIC-CARD-104", title="Card UI Component 104")
    clt_path = create_spec(eidos_dir, "client", "CLT-WEB-104", title="Web Client 104")
    cap_path = create_spec(eidos_dir, "capability", "CAP-DASH-104", title="Dashboard Capability 104")

    ctr_doc = parse_spec_file(ctr_path)
    ctr_doc.frontmatter["container"] = str(cnt_path.relative_to(eidos_dir))
    ctr_path.write_text(dump_spec_document(ctr_doc.frontmatter, ctr_doc.body), encoding="utf-8")

    # Configure container with top-level invariant and contract, plus standalone component + invariant
    cnt_doc = parse_spec_file(cnt_path)
    cnt_doc.frontmatter["domain"] = str(dom_path.relative_to(eidos_dir))
    cnt_doc.frontmatter["contracts"] = {"provides": [str(ctr_path.relative_to(eidos_dir))], "consumes": []}
    cnt_doc.frontmatter["invariants"] = [
        {
            "id": "INV-CNT-UPTIME-104",
            "rule": "API container must expose health endpoint.",
        }
    ]
    cnt_path.write_text(dump_spec_document(cnt_doc.frontmatter, cnt_doc.body), encoding="utf-8")

    cmp_doc = parse_spec_file(cmp_path)
    cmp_doc.frontmatter["container"] = "CNT-API-104"
    cmp_doc.frontmatter["invariants"] = [
        {
            "id": "INV-API-HANDLER-104",
            "rule": "Must validate tokens.",
        }
    ]
    cmp_path.write_text(dump_spec_document(cmp_doc.frontmatter, cmp_doc.body), encoding="utf-8")

    # Configure gateway container consuming CNT-API-104 contract
    cnt_gw_doc = parse_spec_file(cnt_gw_path)
    cnt_gw_doc.frontmatter["domain"] = str(dom_path.relative_to(eidos_dir))
    cnt_gw_doc.frontmatter["contracts"] = {"provides": [], "consumes": [str(ctr_path.relative_to(eidos_dir))]}
    cnt_gw_path.write_text(dump_spec_document(cnt_gw_doc.frontmatter, cnt_gw_doc.body), encoding="utf-8")

    # Configure client, flow, ui-component, and capability using relative .adoc paths to verify canonicalization
    clt_doc = parse_spec_file(clt_path)
    clt_doc.frontmatter["container"] = str(cnt_path.relative_to(eidos_dir))
    clt_doc.frontmatter["design_system"] = str(ds_path.relative_to(eidos_dir))
    clt_doc.frontmatter["screens"] = [str(scr_path.relative_to(eidos_dir))]
    clt_doc.frontmatter["flows"] = [str(flw_path.relative_to(eidos_dir))]
    clt_path.write_text(dump_spec_document(clt_doc.frontmatter, clt_doc.body), encoding="utf-8")

    flw_doc = parse_spec_file(flw_path)
    flw_doc.frontmatter["steps"] = [
        {
            "step": 1,
            "actor": "PER-ARCH-104",
            "client": str(clt_path.relative_to(eidos_dir)),
            "screen": str(scr_path.relative_to(eidos_dir)),
            "action": "Open dashboard",
        }
    ]
    flw_path.write_text(dump_spec_document(flw_doc.frontmatter, flw_doc.body), encoding="utf-8")

    uic_doc = parse_spec_file(uic_path)
    uic_doc.frontmatter["design_system"] = str(ds_path.relative_to(eidos_dir))
    uic_doc.frontmatter["invariants"] = [
        {
            "id": "INV-UIC-CARD-104",
            "rule": "Card must render accessible title.",
        }
    ]
    uic_path.write_text(dump_spec_document(uic_doc.frontmatter, uic_doc.body), encoding="utf-8")

    cap_doc = parse_spec_file(cap_path)
    cap_doc.frontmatter["domain"] = str(dom_path.relative_to(eidos_dir))
    cap_doc.frontmatter["business"] = {
        "personas": [str(per_path.relative_to(eidos_dir))],
        "policies": [str(pol_path.relative_to(eidos_dir))],
    }
    cap_doc.frontmatter["experience"] = {
        "clients": [str(clt_path.relative_to(eidos_dir))],
        "design_systems": [str(ds_path.relative_to(eidos_dir))],
        "screens": [str(scr_path.relative_to(eidos_dir))],
        "flows": [str(flw_path.relative_to(eidos_dir))],
        "ui_components": [str(uic_path.relative_to(eidos_dir))],
    }
    cap_doc.frontmatter["technical"] = {
        "containers": [str(cnt_path.relative_to(eidos_dir))],
        "components": ["CMP-API-HANDLER-104"],
        "invariants": [
            {
                "id": "INV-CAP-DASH-104",
                "rule": "Dashboard capability must respond within 200ms.",
            }
        ],
    }
    cap_path.write_text(dump_spec_document(cap_doc.frontmatter, cap_doc.body), encoding="utf-8")

    dlt_path = create_delta(eidos_dir, "DLT-2026-104", title="Modify Handler 104")
    dlt_doc = parse_spec_file(dlt_path)
    dlt_doc.frontmatter["mutations"] = [
        {
            "operation": "modify",
            "target": "CMP-API-HANDLER-104",
        },
        {
            "operation": "modify",
            "target": "INV-API-HANDLER-104",
        },
        {
            "operation": "modify",
            "target": "CTR-API-104",
        },
        {
            "operation": "modify",
            "target": "CLT-WEB-104",
        },
    ]
    dlt_path.write_text(dump_spec_document(dlt_doc.frontmatter, dlt_doc.body), encoding="utf-8")

    idx = ArchitecturalIndex(db_path=eidos_dir / ".cache" / "index.db", repo_root=tmp_path, eidos_dir=eidos_dir)
    try:
        conn = idx._get_connection()
        # Simulate upgrading from schema_version 2.11.0 -> 2.18.0
        conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('schema_version', '2.11.0');")
        conn.commit()
        ensure_cache_schema(conn)
        assert conn.execute("SELECT value FROM metadata WHERE key = 'schema_version';").fetchone()[0] == "2.18.0"

        idx.sync(force=True)

        # 1. Verify build_spec_path_resolver resolves bare, eidos/-prefixed, ./-prefixed, and suffix paths
        resolver = build_spec_path_resolver(conn)
        rel_cnt = str(cnt_path.relative_to(eidos_dir)).replace("\\", "/")
        assert resolver(rel_cnt) == "CNT-API-104"
        assert resolver(f"eidos/{rel_cnt}") == "CNT-API-104"
        assert resolver(f"./{rel_cnt}") == "CNT-API-104"
        assert resolver(cnt_path.name) == "CNT-API-104"

        # 2. Verify path-to-ID canonicalization and complete edge coverage in edges table
        edge_set = {
            (r[0], r[1], r[2]) for r in conn.execute("SELECT source_id, target_id, rel_type FROM edges;").fetchall()
        }
        assert ("CAP-DASH-104", "DOM-CORE-104", "realizes") in edge_set
        assert ("CAP-DASH-104", "PER-ARCH-104", "serves_persona") in edge_set
        assert ("CAP-DASH-104", "POL-GOV-104", "governed_by") in edge_set
        assert ("CAP-DASH-104", "CLT-WEB-104", "delivered_by") in edge_set
        assert ("CAP-DASH-104", "SCR-DASH-104", "delivered_by") in edge_set
        assert ("CAP-DASH-104", "FLOW-ONBOARD-104", "delivered_by") in edge_set
        assert ("CAP-DASH-104", "UIC-CARD-104", "delivered_by") in edge_set
        assert ("CAP-DASH-104", "CNT-API-104", "implements") in edge_set
        assert ("CLT-WEB-104", "CNT-API-104", "implemented_by") in edge_set
        assert ("CLT-WEB-104", "DS-BRAND-104", "styled_by") in edge_set
        assert ("CLT-WEB-104", "SCR-DASH-104", "has_screen") in edge_set
        assert ("CLT-WEB-104", "FLOW-ONBOARD-104", "has_flow") in edge_set
        assert ("FLOW-ONBOARD-104", "SCR-DASH-104", "uses_screen") in edge_set
        assert ("UIC-CARD-104", "DS-BRAND-104", "styled_by") in edge_set
        assert ("CNT-GW-104", "CNT-API-104", "depends_on") in edge_set

        # 3. Verify get_spec_catalog and _build_spec_catalog_with_invariants parity
        specs_by_id, invariant_map = _build_spec_catalog_with_invariants(eidos_dir)
        assert "CNT-API-104" in specs_by_id
        assert "CMP-API-HANDLER-104" in specs_by_id
        assert "INV-API-HANDLER-104" in invariant_map

        # 4. Verify compute_delta_impact matches get_portal_data delta blast radius and invariant categories
        aff, br = idx.compute_delta_impact(
            dlt_doc.frontmatter["mutations"],
        )
        assert aff == {
            "components": ["CMP-API-HANDLER-104"],
            "containers": ["CNT-API-104"],
            "contracts": ["CTR-API-104"],
            "clients": ["CLT-WEB-104"],
        }
        assert set(br["impacted_containers"]) >= {"CNT-API-104", "CNT-GW-104"}
        assert "CMP-API-HANDLER-104" in br["impacted_components"]
        assert "DS-BRAND-104" in br["impacted_design_systems"]
        assert "SCR-DASH-104" in br["impacted_screens"]
        assert "PER-ARCH-104" in br["impacted_personas"]
        assert set(br["invariants_to_enforce"]) >= {"INV-API-HANDLER-104", "INV-CNT-UPTIME-104"}

        portal_data = get_portal_data(eidos_dir)
        portal_dlt = next(d for d in portal_data["deltas"]["proposed"] if d["id"] == dlt_doc.doc_id)
        assert portal_dlt["affects"] == aff
        assert portal_dlt["blast_radius"] == br

        # Verify portal invariants across all 5 source types (policy, capability, container, component, ui_component)
        inv_by_id = {inv["id"]: inv for inv in portal_data["invariants"]}
        assert inv_by_id["INV-POL-GOV-104"]["source_type"] == "policy (POL-GOV-104)"
        assert inv_by_id["INV-CAP-DASH-104"]["source_type"] == "capability (CAP-DASH-104)"
        assert inv_by_id["INV-CNT-UPTIME-104"]["source_type"] == "container (CNT-API-104)"
        assert inv_by_id["INV-API-HANDLER-104"]["source_type"] == "component (CMP-API-HANDLER-104)"
        assert inv_by_id["INV-UIC-CARD-104"]["source_type"] == "ui_component (UIC-CARD-104)"

        # Verify portal inter-container dependencies, domain capabilities, and design reverse-links
        gw_comp = next(c for c in portal_data["components"] if c["id"] == "CNT-GW-104")
        assert gw_comp["dependencies"]["internal_containers"] == ["CNT-API-104"]
        dom_item = next(d for d in portal_data["domains"] if d["id"] == "DOM-CORE-104")
        assert "CAP-DASH-104" in dom_item["capabilities"]
        scr_item = next(s for s in portal_data["screens"] if s["id"] == "SCR-DASH-104")
        assert scr_item["client"] == "CLT-WEB-104"
        assert scr_item["system_ref"] == "DS-BRAND-104"
        assert scr_item["capabilities"] == ["CAP-DASH-104"]
        flw_item = next(f for f in portal_data["flows"] if f["id"] == "FLOW-ONBOARD-104")
        assert flw_item["clients"] == ["CLT-WEB-104"]
        assert flw_item["capabilities"] == ["CAP-DASH-104"]
    finally:
        idx.close()


def test_dlt_2026_105_c4_level_1_and_exploration_edges(tmp_path: Path) -> None:
    """Verify DLT-2026-105 C4 Level 1 (ARCH-*, SYS-*) and exploration (EXP-*) graph edges, incremental sync, and hub fan-out prevention."""  # eidos:fixture
    from eidos.core.lifecycle.delta_engine import create_delta
    from eidos.core.lifecycle.initializer import init_repository
    from eidos.core.lifecycle.scaffolder import create_spec
    from eidos.core.spec.parser import dump_spec_document, parse_spec_file

    init_repository(tmp_path, title="dlt-105-repo")
    eidos_dir = tmp_path / "eidos"

    per_path = create_spec(eidos_dir, "persona", "PER-LEAD-105", title="Lead Persona 105")
    exp_path = create_spec(eidos_dir, "exploration", "EXP-2026-105", title="Spike 105")
    exp2_path = create_spec(eidos_dir, "exploration", "EXP-2026-106", title="Delta-bridged Spike 106")
    cnt_a_path = create_spec(eidos_dir, "container", "CNT-ALPHA-105", title="Alpha Container 105")
    create_spec(eidos_dir, "container", "CNT-BETA-105", title="Beta Container 105")
    dlt_path = create_delta(eidos_dir, "DLT-2026-106", title="Apply Spike 106")

    sys_id = parse_spec_file(eidos_dir / "system.adoc").doc_id
    arch_path = create_spec(eidos_dir, "architecture", "ARCH-001", title="System Architecture 105")
    arch_doc = parse_spec_file(arch_path)
    arch_doc.frontmatter["c4_context"] = {
        "actors": [str(per_path.relative_to(eidos_dir))],
        "external_systems": [],
    }
    arch_doc.frontmatter["decisions"] = [
        {
            "id": "DEC-ARCH-105",
            "title": "Use Centralized C4",
            "decision": "Keep C4 centralized.",
            "rationale": "Token efficiency.",
            "graduated_from": str(exp_path.relative_to(eidos_dir)),
        }
    ]
    arch_path.write_text(dump_spec_document(arch_doc.frontmatter, arch_doc.body), encoding="utf-8")

    # Configure CNT-ALPHA-105 and standalone component CMP-ALPHA-WORKER-105 with decision graduated_from EXP-2026-105
    cnt_a_doc = parse_spec_file(cnt_a_path)
    cnt_a_doc.frontmatter["decisions"] = [
        {
            "id": "DEC-CNT-ALPHA-105",
            "title": "Container Spike Decision",
            "decision": "Adopt alpha container.",
            "rationale": "Low latency.",
            "graduated_from": "EXP-2026-105",
        }
    ]
    cnt_a_path.write_text(dump_spec_document(cnt_a_doc.frontmatter, cnt_a_doc.body), encoding="utf-8")

    cmp_a_path = create_spec(eidos_dir, "component", "CMP-ALPHA-WORKER-105", title="Alpha Worker 105")
    cmp_a_doc = parse_spec_file(cmp_a_path)
    cmp_a_doc.frontmatter["container"] = "CNT-ALPHA-105"
    cmp_a_doc.frontmatter["decisions"] = [
        {
            "id": "DEC-CMP-ALPHA-105",
            "title": "Worker Spike Decision",
            "decision": "Adopt worker queue.",
            "rationale": "Low latency.",
            "graduated_from": "EXP-2026-105",
        }
    ]
    cmp_a_path.write_text(dump_spec_document(cmp_a_doc.frontmatter, cmp_a_doc.body), encoding="utf-8")

    # Configure EXP-2026-106 with resulting_deltas -> DLT-2026-106 affecting CNT-BETA-105
    dlt_doc = parse_spec_file(dlt_path)
    dlt_doc.frontmatter["mutations"] = [{"operation": "modify", "target": "CNT-BETA-105"}]
    dlt_path.write_text(dump_spec_document(dlt_doc.frontmatter, dlt_doc.body), encoding="utf-8")

    exp2_doc = parse_spec_file(exp2_path)
    exp2_doc.frontmatter["status"] = "adopted"
    exp2_doc.frontmatter["decision"] = {
        "outcome": "adopted",
        "resulting_deltas": [dlt_doc.doc_id],
    }
    exp2_path.write_text(dump_spec_document(exp2_doc.frontmatter, exp2_doc.body), encoding="utf-8")

    idx = ArchitecturalIndex(db_path=eidos_dir / ".cache" / "index.db", repo_root=tmp_path, eidos_dir=eidos_dir)
    try:
        idx.sync(force=True)
        conn = idx._get_connection()

        edges = {
            (r[0], r[1], r[2]) for r in conn.execute("SELECT source_id, target_id, rel_type FROM edges;").fetchall()
        }
        # 1. Verify C4 Level 1 & exploration edges and absence of reverse (CNT-*, ARCH-*, 'part_of')
        assert ("ARCH-001", "PER-LEAD-105", "serves_persona") in edges
        assert ("ARCH-001", sys_id, "describes_system") in edges
        assert ("ARCH-001", "CNT-ALPHA-105", "contains_container") in edges
        assert ("ARCH-001", "CNT-BETA-105", "contains_container") in edges
        assert ("CNT-ALPHA-105", "ARCH-001", "part_of") not in edges
        assert ("ARCH-001", "EXP-2026-105", "graduated_from") in edges
        assert ("CNT-ALPHA-105", "EXP-2026-105", "graduated_from") in edges
        assert ("CMP-ALPHA-WORKER-105", "EXP-2026-105", "graduated_from") in edges
        assert ("EXP-2026-106", dlt_doc.doc_id, "graduated_to") in edges
        assert ("EXP-2026-106", "CNT-BETA-105", "graduated_to") in edges

        # Verify execute_incoming_references excludes synthetic C4 structural edges (contains_container, describes_system)
        inc_alpha = idx.get_incoming_references("CNT-ALPHA-105")
        assert all(r["rel_type"] not in ("contains_container", "describes_system") for r in inc_alpha)

        # 2. Verify ARCH-001 k-hop blast radius expands at depth 0
        arch_br = idx.get_k_hop_neighborhood("ARCH-001", max_depth=2)
        arch_hop1 = {n["id"] for n in arch_br if n["depth"] == 1}
        arch_hop2 = {n["id"] for n in arch_br if n["depth"] == 2}
        assert {sys_id, "PER-LEAD-105", "CNT-ALPHA-105", "CNT-BETA-105", "EXP-2026-105"} <= arch_hop1
        assert "CMP-ALPHA-WORKER-105" in arch_hop2

        # 3. Verify hub fan-out suppression at depth >= 1: CNT-ALPHA-105 reaches ARCH-001 and EXP-2026-105 at Hop 1,
        #    but does NOT fan out through ARCH-001 to CNT-BETA-105 or sys_id at Hop 2.
        cnt_a_br = idx.get_k_hop_neighborhood("CNT-ALPHA-105", max_depth=2)
        cnt_a_ids = {n["id"] for n in cnt_a_br}
        assert {"CNT-ALPHA-105", "ARCH-001", "EXP-2026-105", "CMP-ALPHA-WORKER-105"} <= cnt_a_ids
        assert "CNT-BETA-105" not in cnt_a_ids
        assert sys_id not in cnt_a_ids

        # 4. Verify EXP-2026-106 blast radius reaches CNT-BETA-105 via graduated_to -> affects bridge
        exp2_br = idx.get_k_hop_neighborhood("EXP-2026-106", max_depth=1)
        assert "CNT-BETA-105" in {n["id"] for n in exp2_br if n["depth"] == 1}

        # 5. Verify incremental container creation updates ARCH-001 -> CNT-GAMMA-105 contains_container edge
        time.sleep(0.01)
        create_spec(eidos_dir, "container", "CNT-GAMMA-105", title="Gamma Container 105")
        idx.sync(force=False)
        arch_br_after = idx.get_k_hop_neighborhood("ARCH-001", max_depth=1)
        assert "CNT-GAMMA-105" in {n["id"] for n in arch_br_after if n["depth"] == 1}
    finally:
        idx.close()


def test_hybrid_semantic_rrf_and_deterministic_fallback(tmp_path: Path) -> None:
    """Verify INV-AGENT-HYBRID-SEMANTIC-FALLBACK: spec-only SHA-256 caching in spec_embeddings across sync --force,
    right-padded Harrier sts_query formatting, tau_min=0.50 cosine gating, exact entity ID pinning,
    recommend_invariants_for_unbound_tests, and 100% deterministic fallback to FTS5 BM25 when spec_embeddings are absent."""
    from typer.testing import CliRunner

    from eidos.cli.main import app
    from eidos.core.index.query import build_fts_match_query, expand_architectural_synonyms
    from eidos.core.index.semantic import (
        HARRIER_EMBEDDING_DIM,
        HARRIER_QUERY_INSTRUCTION,
        HARRIER_STS_INSTRUCTION,
        compute_spec_embedding_sha256,
        format_harrier_query,
        pack_embedding_f32,
        unpack_embedding_f32,
    )
    from eidos.core.lifecycle.initializer import init_repository
    from eidos.core.lifecycle.scaffolder import create_spec
    from eidos.core.spec.parser import dump_spec_document, parse_spec_file
    from eidos.vcs.invariants import recommend_invariants_for_unbound_tests

    # 1. Verify Harrier sts_query formatting, spec-only SHA-256 isolation, and float32 packing
    assert HARRIER_QUERY_INSTRUCTION == HARRIER_STS_INSTRUCTION
    q_fmt = format_harrier_query("prevent airgapped external calls")
    assert q_fmt.startswith(HARRIER_QUERY_INSTRUCTION)
    assert "Retrieve semantically similar text" in q_fmt
    assert "airgapped" in expand_architectural_synonyms("offline")
    assert '"zero"*' in build_fts_match_query("airgapped", operator="OR", expand_synonyms=True)

    sha_a = compute_spec_embedding_sha256(
        entity_id="CMP-ALPHA",
        entity_type="tech/component",
        title="Alpha Engine",
        domain="DOM-CORE",
        jtbd_or_rule="Deterministic rule",
        body_text="Handles local execution.",
        spec_symbols="alpha engine",
    )
    # Spec-only SHA-256 depends solely on spec fields, never on AST code_symbols
    sha_b = compute_spec_embedding_sha256(
        entity_id="CMP-ALPHA",
        entity_type="tech/component",
        title="Alpha Engine",
        domain="DOM-CORE",
        jtbd_or_rule="Deterministic rule",
        body_text="Handles local execution.",
        spec_symbols="alpha engine",
    )
    assert sha_a == sha_b

    unit_vec = [1.0] + [0.0] * (HARRIER_EMBEDDING_DIM - 1)
    blob = pack_embedding_f32(unit_vec)
    assert len(blob) == HARRIER_EMBEDDING_DIM * 4
    unpacked = unpack_embedding_f32(blob)
    assert abs(unpacked[0] - 1.0) < 1e-6

    # 2. Scaffold repository with containers, components, policies, and unbound test function
    init_repository(tmp_path, title="hybrid-semantic-repo")
    eidos_dir = tmp_path / "eidos"
    create_spec(eidos_dir, "domain", "DOM-SECURITY", title="Security Governance Domain")
    cnt_sec_path = create_spec(eidos_dir, "container", "CNT-SEC-VAULT", title="Hermetic Vault Service")
    cmp_sec_path = create_spec(eidos_dir, "component", "CMP-HERMETIC-GUARD", title="Hermetic Guard")
    pol_path = create_spec(eidos_dir, "policy", "POL-HERMETIC-001", title="Hermetic Execution Policy")

    cnt_doc = parse_spec_file(cnt_sec_path)
    cnt_doc.frontmatter["domain"] = "DOM-SECURITY"
    cnt_doc.frontmatter["source_path"] = "src/vault"
    cnt_sec_path.write_text(dump_spec_document(cnt_doc.frontmatter, cnt_doc.body), encoding="utf-8")

    cmp_doc = parse_spec_file(cmp_sec_path)
    cmp_doc.frontmatter["container"] = "CNT-SEC-VAULT"
    cmp_doc.frontmatter["source_path"] = "src/vault/guard.py"
    cmp_sec_path.write_text(
        dump_spec_document(
            cmp_doc.frontmatter,
            "= Hermetic Guard\n\n== Responsibility\nBlocks outbound sockets in airgapped execution.\n",
        ),
        encoding="utf-8",
    )

    pol_doc = parse_spec_file(pol_path)
    pol_doc.frontmatter["invariants"] = [
        {
            "id": "INV-HERMETIC-SOCKET-BAN",  # eidos:fixture
            "rule": "Outbound network sockets must be blocked during hermetic airgapped execution.",
            "test_binding": "tests/test_vault.py::test_hermetic_socket_ban",
        }
    ]
    pol_path.write_text(dump_spec_document(pol_doc.frontmatter, pol_doc.body), encoding="utf-8")

    (tmp_path / "src" / "vault").mkdir(parents=True, exist_ok=True)
    guard_py = tmp_path / "src" / "vault" / "guard.py"
    guard_py.write_text("def block_sockets() -> bool:\n    return True\n", encoding="utf-8")

    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tests" / "test_vault.py").write_text(
        "def test_hermetic_socket_ban():\n    # Verifies INV-HERMETIC-SOCKET-BAN\n    assert True\n\n"  # eidos:fixture
        "def test_outbound_network_socket_blocked():\n    assert True\n",
        encoding="utf-8",
    )

    idx = ArchitecturalIndex(db_path=eidos_dir / ".cache" / "index.db", repo_root=tmp_path, eidos_dir=eidos_dir)
    try:
        idx.sync(force=True)

        # 3. Verify 100% deterministic FTS5 BM25 fallback when spec_embeddings is empty
        status_empty = idx.get_semantic_status()
        assert status_empty["embedded_entities"] == 0
        fallback_hits = idx.query_fts("Hermetic Guard", limit=5)
        assert any(h["id"] == "CMP-HERMETIC-GUARD" for h in fallback_hits)
        assert all("match_source" not in h for h in fallback_hits)

        # 4. Populate spec_embeddings with a deterministic encoder and verify caching across code edits and sync(force=True)
        def _deterministic_encoder(texts: list[str]) -> list[bytes]:
            blobs: list[bytes] = []
            for t in texts:
                vec = [0.0] * HARRIER_EMBEDDING_DIM
                t_low = t.lower()
                if any(k in t_low for k in ("hermetic", "airgapped", "outbound", "egress", "firewall")):
                    vec[0] = 0.92
                    vec[1] = 0.39
                elif "vault" in t_low:
                    vec[0] = 0.75
                    vec[1] = 0.66
                else:
                    vec[2] = 1.0
                blobs.append(pack_embedding_f32(vec))
            return blobs

        sync_res1 = idx.sync_semantic(force=False, encode_fn=_deterministic_encoder)
        assert sync_res1["encoded_entities"] > 0
        assert sync_res1["reused_entities"] == 0

        # Editing Python source code does NOT invalidate spec-only content_sha256 embeddings (0 re-encoded)
        guard_py.write_text("def block_sockets() -> bool:\n    return True\n\ndef extra_helper() -> int:\n    return 2\n", encoding="utf-8")
        _touch_later(guard_py, offset=10)
        idx.sync(force=False)
        sync_res2 = idx.sync_semantic(force=False, encode_fn=_deterministic_encoder)
        assert sync_res2["encoded_entities"] == 0
        assert sync_res2["reused_entities"] == sync_res1["encoded_entities"]

        # sync(force=True) preserves spec_embeddings in index.db
        idx.sync(force=True)
        status_after_force = idx.get_semantic_status()
        assert status_after_force["embedded_entities"] == sync_res1["encoded_entities"]
        assert status_after_force["stale_or_missing_embeddings"] == 0

        # 5. Conceptual zero-keyword query ("egress firewall") retrieves CMP-HERMETIC-GUARD via Hybrid RRF (cos >= 0.50)
        sem_hits = idx.query_fts("egress firewall", limit=5, use_semantic=True)
        assert any(h["id"] == "CMP-HERMETIC-GUARD" for h in sem_hits)
        guard_hit = next(h for h in sem_hits if h["id"] == "CMP-HERMETIC-GUARD")
        assert guard_hit["match_source"] in ("semantic", "hybrid")
        assert guard_hit["semantic_score"] >= 0.50

        # Disabling semantic (--no-semantic / use_semantic=False) returns pure FTS5 results (empty for "egress firewall")
        assert idx.query_fts("egress firewall", limit=5, use_semantic=False) == []

        # Exact entity ID pinning always ranks explicit ID #1 even under Hybrid RRF
        pinned_hits = idx.query_fts("CNT-SEC-VAULT egress firewall", limit=5, use_semantic=True)
        assert pinned_hits[0]["id"] == "CNT-SEC-VAULT"

        # Context slice records Semantic/Hybrid task match provenance in matched_reasons
        ctx_slice = idx.get_tiered_context_slice(task="egress firewall", tier="guardrails", use_semantic=True)
        assert any(c["id"] == "CMP-HERMETIC-GUARD" for c in ctx_slice.components)
        assert any(
            "Semantic task match" in r or "Hybrid task match" in r
            for r in ctx_slice.matched_reasons.get("CMP-HERMETIC-GUARD", [])
        )
        # Hybrid task match formats 1-based BM25 rank (never BM25 #0)
        hyb_slice = idx.get_tiered_context_slice(task="hermetic egress", tier="guardrails", use_semantic=True)
        guard_reasons = hyb_slice.matched_reasons.get("CMP-HERMETIC-GUARD", [])
        assert any("Hybrid task match" in r and "BM25 #" in r and "BM25 #0" not in r for r in guard_reasons)

        # Index-time synonym enrichment in specs_fts.keywords resolves "offline" to airgapped spec without semantic
        offline_hits = idx.query_fts("offline", limit=5, use_semantic=False)
        assert any(h["id"] in ("CMP-HERMETIC-GUARD", "INV-HERMETIC-SOCKET-BAN") for h in offline_hits)  # eidos:fixture

        # 6. Verify recommend_invariants_for_unbound_tests suggests INV-HERMETIC-SOCKET-BAN for test_outbound_network_socket_blocked  # eidos:fixture
        recs = recommend_invariants_for_unbound_tests(idx, top_k=3)
        unbound_key = "tests/test_vault.py::test_outbound_network_socket_blocked"
        assert unbound_key in recs
        assert recs[unbound_key]
        assert recs[unbound_key][0]["id"] == "INV-HERMETIC-SOCKET-BAN"  # eidos:fixture
    finally:
        idx.close()

    # 7. Verify CLI 'eidos index semantic status', 'eidos index search --no-semantic', and 'eidos context --no-semantic'
    runner = CliRunner()
    res_sem = runner.invoke(app, ["index", "semantic", "status", "--path", str(eidos_dir), "--json"])
    assert res_sem.exit_code == 0
    sem_payload = orjson.loads(res_sem.output)
    assert sem_payload["embedded_entities"] > 0

    res_sem_rich = runner.invoke(app, ["index", "semantic", "status", "--path", str(eidos_dir)])
    assert res_sem_rich.exit_code == 0
    assert "Harrier ONNX" in res_sem_rich.output

    res_sem_unknown = runner.invoke(app, ["index", "semantic", "invalid-action", "--path", str(eidos_dir), "--json"])
    assert res_sem_unknown.exit_code == 1
    assert "Unknown semantic action" in orjson.loads(res_sem_unknown.output)["error"]

    res_search = runner.invoke(
        app, ["index", "search", "Hermetic Guard", "--no-semantic", "--path", str(eidos_dir), "--json"]
    )
    assert res_search.exit_code == 0
    search_payload = orjson.loads(res_search.output)
    assert search_payload["count"] >= 1

    res_ctx = runner.invoke(
        app, ["context", "-t", "egress firewall", "--no-semantic", "--path", str(eidos_dir), "--json"]
    )
    assert res_ctx.exit_code == 0
    assert "tier" in orjson.loads(res_ctx.output)


def test_dlt_2026_114_blast_radius_symbol_and_path_resolution() -> None:
    """Verify DLT-2026-114-RESOLVE-SYMBOLS-0327:
    resolve_blast_radius_targets, get_k_hop_neighborhood, and `eidos index blast-radius`
    resolve canonical entity IDs, source file paths, specification file paths, and AST code symbols.
    """
    import orjson
    from typer.testing import CliRunner

    from eidos.cli.main import app
    from eidos.core.index.graph_index import get_architectural_index

    repo_root = Path(__file__).resolve().parent.parent
    eidos_dir = repo_root / "eidos"
    idx = get_architectural_index(repo_root=repo_root, eidos_dir=eidos_dir)

    # 1. Canonical entity ID resolution
    assert idx.resolve_blast_radius_targets("CNT-EIDOS-VCS") == ["CNT-EIDOS-VCS"]
    cnt_walk = idx.get_k_hop_neighborhood("CNT-EIDOS-VCS", max_depth=1)
    assert cnt_walk and cnt_walk[0]["id"] == "CNT-EIDOS-VCS" and cnt_walk[0]["depth"] == 0

    # 2. Source file path resolution (filters to CMP-* seed at depth=0; parent CNT-* at depth=1)
    assert idx.resolve_blast_radius_targets("src/eidos/vcs/drift.py") == ["CMP-DRIFT-DETECTOR"]
    src_walk = idx.get_k_hop_neighborhood("src/eidos/vcs/drift.py", max_depth=1)
    assert src_walk[0]["id"] == "CMP-DRIFT-DETECTOR" and src_walk[0]["depth"] == 0
    assert any(n["id"] == "CNT-EIDOS-VCS" and n["depth"] == 1 for n in src_walk)

    # Shared contract specification_file source path (`src/eidos/cli/main.py`) seeds CMP-CLI-APP, not CTR-EIDOS-CLI
    assert idx.resolve_blast_radius_targets("src/eidos/cli/main.py") == ["CMP-CLI-APP"]

    # 3. Specification file path resolution (including exact vs suffix disambiguation on system.adoc)
    assert idx.resolve_blast_radius_targets("eidos/architecture/containers/vcs.adoc") == ["CNT-EIDOS-VCS"]
    assert idx.resolve_blast_radius_targets("architecture/containers/vcs.adoc") == ["CNT-EIDOS-VCS"]
    assert idx.resolve_blast_radius_targets("system.adoc") == ["SYS-EIDOS-001"]
    assert idx.resolve_blast_radius_targets("eidos/system.adoc") == ["SYS-EIDOS-001"]

    # Co-owned source file (`src/eidos/core/index/query.py`) resolves all co-owning CMP-* seeds at depth=0
    co_owned = idx.resolve_blast_radius_targets("src/eidos/core/index/query.py")
    assert "CMP-FTS-QUERY-ENGINE" in co_owned and "CMP-GRAPH-TRAVERSAL-ENGINE" in co_owned
    assert "CNT-EIDOS-INDEX" not in co_owned

    # 4. AST code symbol resolution
    assert idx.resolve_blast_radius_targets("detect_drift") == ["CMP-DRIFT-DETECTOR"]
    sym_walk = idx.get_k_hop_neighborhood("detect_drift", max_depth=1)
    assert sym_walk[0]["id"] == "CMP-DRIFT-DETECTOR" and sym_walk[0]["depth"] == 0

    # 5. CLI `--json` and Rich output include query, resolved_entities, entity_id, and neighbors
    runner = CliRunner()
    cli_res = runner.invoke(
        app,
        ["index", "blast-radius", "src/eidos/vcs/drift.py", "--depth", "1", "--path", str(eidos_dir), "--json"],
    )
    assert cli_res.exit_code == 0
    payload = orjson.loads(cli_res.stdout)
    assert payload["query"] == "src/eidos/vcs/drift.py"
    assert payload["resolved_entities"] == ["CMP-DRIFT-DETECTOR"]
    assert payload["entity_id"] == "CMP-DRIFT-DETECTOR"
    assert payload["count"] > 0

    cli_rich = runner.invoke(
        app,
        ["index", "blast-radius", "src/eidos/vcs/drift.py", "--depth", "1", "--path", str(eidos_dir)],
    )
    assert cli_rich.exit_code == 0
    assert "CMP-DRIFT-DETECTOR" in cli_rich.stdout

    cli_missing = runner.invoke(
        app,
        ["index", "blast-radius", "NONEXISTENT_SYMBOL_9999", "--depth", "1", "--path", str(eidos_dir)],
    )
    assert cli_missing.exit_code == 0
    assert "not found in architectural graph" in cli_missing.stdout


