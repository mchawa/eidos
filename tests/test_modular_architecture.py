"""Architectural cohesion, disjoint CMP-* source ownership, acyclicity, and facade parity tests (DLT-2026-139)."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

from eidos.core.spec.parser import parse_spec_file


def _collect_internal_eidos_imports(file_path: Path) -> set[str]:
    """Return all top-level ``eidos.*`` module paths imported statically inside ``file_path``."""
    tree = ast.parse(file_path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("eidos."):
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("eidos."):
                    imported.add(alias.name)
    return imported


def _has_cycle(adj: dict[str, set[str]]) -> list[str] | None:
    """Detect any directed cycle in ``adj`` and return the cycle path if found."""
    state: dict[str, int] = {}
    stack: list[str] = []

    def _dfs(node: str) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for nxt in sorted(adj.get(node, ())):
            if nxt not in adj:
                continue
            st = state.get(nxt, 0)
            if st == 1:
                idx = stack.index(nxt)
                return [*stack[idx:], nxt]
            if st == 0:
                found = _dfs(nxt)
                if found is not None:
                    return found
        stack.pop()
        state[node] = 2
        return None

    for n in sorted(adj):
        if state.get(n, 0) == 0:
            found = _dfs(n)
            if found is not None:
                return found
    return None


def test_modular_component_boundaries_and_facade_parity():
    """Verify INV-CORE-MODULAR-COMPONENT-COHESION: disjoint CMP-* source_path bindings, acyclic C4 intra-container graphs, and facade import parity."""
    repo_root = Path(__file__).resolve().parent.parent
    comp_dir = repo_root / "eidos" / "architecture" / "components"
    assert comp_dir.is_dir()

    # 1. Every active tech/component spec in Eidos has mutually disjoint source_path entries on disk
    by_path: dict[str, list[str]] = {}
    module_to_cmp: dict[str, str] = {}
    cmp_to_cnt: dict[str, str] = {}
    for p in sorted(comp_dir.glob("*.adoc")):
        doc = parse_spec_file(p)
        fm = doc.frontmatter
        status = str(fm.get("status", "active")).strip().lower()
        if status in ("deprecated", "retired"):
            continue
        cid = str(fm.get("id", "")).strip()
        cnt = str(fm.get("container", "")).strip()
        cmp_to_cnt[cid] = cnt
        sp = fm.get("source_path")
        entries = [sp] if isinstance(sp, str) else list(sp or [])
        for entry in entries:
            norm = str(entry).replace("\\", "/").strip().strip("/")
            by_path.setdefault(norm, []).append(cid)
            if norm.startswith("src/") and norm.endswith(".py"):
                mod = norm.removeprefix("src/").removesuffix(".py").replace("/", ".")
                if mod.endswith(".__init__"):
                    mod = mod.removesuffix(".__init__")
                module_to_cmp[mod] = cid

    overlaps = {path: ids for path, ids in by_path.items() if len(ids) > 1}
    assert not overlaps, f"Overlapping CMP-* source_path bindings detected: {overlaps}"

    # Verify the newly promoted and disambiguated C4 components exist with disjoint ownership
    expected_components = {
        "CMP-CONTRACT-PARITY-VALIDATOR",
        "CMP-ARCHITECTURE-BOUNDARY-VALIDATOR",
        "CMP-HTTP-TOPOLOGY-LINKER",
        "CMP-AST-DIFF-CLASSIFIER",
        "CMP-GIT-DIFF-INSPECTOR",
        "CMP-INVARIANT-TRACEABILITY-VERIFIER",
        "CMP-FTS-QUERY-ENGINE",
        "CMP-GRAPH-TRAVERSAL-ENGINE",
        "CMP-HOOK-MANAGER",
    }
    assert expected_components <= set(cmp_to_cnt)

    # 2. Refactored intra-container component graphs remain acyclic (no inverted C4 dependency arrows)
    for target_cnt in ("CNT-EIDOS-CORE", "CNT-EIDOS-VCS", "CNT-EIDOS-PLUGIN", "CNT-EIDOS-PORTAL"):
        cnt_cmps = {cid for cid, cnt in cmp_to_cnt.items() if cnt == target_cnt}
        adj: dict[str, set[str]] = {cid: set() for cid in cnt_cmps}
        for mod, src_cmp in module_to_cmp.items():
            if src_cmp not in cnt_cmps:
                continue
            rel_py = repo_root / "src" / (mod.replace(".", "/") + ".py")
            if not rel_py.is_file():
                continue
            for imp_mod in _collect_internal_eidos_imports(rel_py):
                dst_cmp = module_to_cmp.get(imp_mod)
                if dst_cmp and dst_cmp in cnt_cmps and dst_cmp != src_cmp:
                    adj[src_cmp].add(dst_cmp)
        cycle = _has_cycle(adj)
        assert cycle is None, f"Circular C4 component dependency in {target_cnt}: {' -> '.join(cycle)}"

    # Verify CNT-EIDOS-INDEX decomposed submodules avoid query <-> sync and ast_indexer <-> ast_diff/ast_http cycles
    index_modules = [
        "eidos.core.index.ast_preprocess",
        "eidos.core.index.ast_imports",
        "eidos.core.index.ast_indexer",
        "eidos.core.index.ast_http",
        "eidos.core.index.ast_diff",
        "eidos.core.index.http_linker",
        "eidos.core.index.code_graph",
        "eidos.core.index.spec_edges",
        "eidos.core.index.sync",
        "eidos.core.index.fts_query",
        "eidos.core.index.traversal",
        "eidos.core.index.context_globs",
        "eidos.core.index.context_models",
        "eidos.core.index.context",
    ]
    idx_adj: dict[str, set[str]] = {m: set() for m in index_modules}
    for mod in index_modules:
        fp = repo_root / "src" / (mod.replace(".", "/") + ".py")
        assert fp.is_file(), f"Missing decomposed index module: {fp}"
        for imp_mod in _collect_internal_eidos_imports(fp):
            if imp_mod in idx_adj and imp_mod != mod:
                idx_adj[mod].add(imp_mod)
    idx_cycle = _has_cycle(idx_adj)
    assert idx_cycle is None, f"Circular module dependency in CNT-EIDOS-INDEX: {' -> '.join(idx_cycle)}"

    # 3. Backward-compatible public facade re-exports resolve identically to their canonical owning modules
    facade_checks = [
        ("eidos.core.spec.validator", "evaluate_import_boundaries", "eidos.core.spec.boundary_validator"),
        ("eidos.core.index.query", "execute_fts_query", "eidos.core.index.fts_query"),
        ("eidos.core.index.query", "execute_k_hop_traversal", "eidos.core.index.traversal"),
        ("eidos.core.index.ast_indexer", "normalize_http_route_path", "eidos.core.index.ast_http"),
        ("eidos.core.index.ast_indexer", "has_structural_changes", "eidos.core.index.ast_diff"),
        ("eidos.core.index.ast_indexer", "classify_design_changes", "eidos.core.index.ast_diff"),
        ("eidos.core.lifecycle.delta_engine", "verify_delta_mutations", "eidos.core.lifecycle.delta_verifier"),
        ("eidos.core.lifecycle.delta_engine", "archive_deltas", "eidos.core.lifecycle.archiver"),
        ("eidos.plugin.installer", "check_target_hook_health", "eidos.plugin.hook_adapters"),
        ("eidos.plugin.installer", "TARGET_HOOK_PATHS", "eidos.plugin.hook_adapters"),
        ("eidos.portal.generator", "_generate_c4_context_mermaid", "eidos.portal.mermaid"),
        ("eidos.vcs.drift", "classify_changed_files", "eidos.vcs.diff"),
        ("eidos.vcs.drift", "extract_invariants_and_verify", "eidos.vcs.invariants"),
    ]
    for facade_mod, attr, canonical_mod in facade_checks:
        f_obj = getattr(importlib.import_module(facade_mod), attr)
        c_obj = getattr(importlib.import_module(canonical_mod), attr)
        if attr != "evaluate_import_boundaries":
            assert f_obj is c_obj, f"Facade {facade_mod}.{attr} diverged from {canonical_mod}.{attr}"
        else:
            assert callable(f_obj) and callable(c_obj)


def test_decomposed_index_submodule_edge_cases(tmp_path):
    """Exercise edge cases across ast_http, context_globs, code_graph, ast_imports, and sync submodules."""
    import sqlite3

    from eidos.core.index.ast_http import (
        _eval_url_expr,
        _join_http_paths,
        normalize_http_route_path,
    )
    from eidos.core.index.ast_indexer import get_ast_indexer
    from eidos.core.index.code_graph import (
        _CodeOwnershipResolver,
        _find_import_line,
        _normalize_rel_path,
        index_source_file,
        store_code_symbols,
    )
    from eidos.core.index.context_globs import (
        _expand_context_file_pattern,
        _expand_single_context_file_pattern,
        split_brace_aware_csv,
    )
    from eidos.core.index.graph_index import ArchitecturalIndex
    from eidos.core.index.storage import ensure_cache_schema
    from eidos.core.index.sync import _is_governed_source_file, check_index_dirty, reconcile_code_ownership

    # 1. ast_http edge cases (concatenated URLs, full URLs, empty/non-path strings)
    assert normalize_http_route_path("") == ""
    assert normalize_http_route_path("https://api.example.com") == "/"
    assert normalize_http_route_path("not_a_url") == ""
    assert _join_http_paths("", "https://api.example.com/v1/users/42") == "/v1/users/42"
    assert _join_http_paths("https://api.example.com/v1", "/orders") == "/v1/orders"
    assert _eval_url_expr("", {}, {}, "") == ""
    assert _eval_url_expr("UNKNOWN_VAR", {}, {}, "") == ""
    assert (
        _eval_url_expr(
            'API_BASE + "/orders/" + orderId',
            {"API_BASE": "/api/v1"},
            {},
            "",
        )
        == "/api/v1/orders/{*}"
    )
    assert (
        _eval_url_expr(
            "API_BASE + `/items/${id}`",
            {"API_BASE": "/api/v1"},
            {},
            "",
        )
        == "/api/v1/items/{*}"
    )
    assert _eval_url_expr("''", {}, {}, "/api/v2") == "/api/v2"
    assert _eval_url_expr("helper(x)", {}, {"helper": ("x", "")}, "") == ""

    # 2. context_globs edge cases (empty CSV, planned brace globs, absolute/./-prefixed patterns, symbol_mappings fallback)
    assert split_brace_aware_csv("") == []
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_cache_schema(conn)
    conn.execute(
        "INSERT INTO symbol_mappings (symbol, entity_id, kind) VALUES ('src/billing', 'CNT-BILL', 'source_path');"
    )
    (tmp_path / "src" / "billing").mkdir(parents=True)
    planned_braces = _expand_context_file_pattern(tmp_path, conn, "src/billing/{new_a,new_b}.py")
    assert planned_braces == ["src/billing/new_a.py", "src/billing/new_b.py"]
    abs_pat = (tmp_path / "src" / "billing" / "*.py").as_posix()
    assert _expand_single_context_file_pattern(tmp_path, conn, abs_pat, existing_only=False)
    assert _expand_single_context_file_pattern(tmp_path, conn, "./src/billing/*.py", existing_only=False)
    assert _expand_single_context_file_pattern(tmp_path, conn, "   ", existing_only=False) == []
    multi_seg = _expand_single_context_file_pattern(tmp_path, conn, "src/missing/*/file.py", existing_only=False)
    assert isinstance(multi_seg, list)
    sym_exact = _expand_single_context_file_pattern(tmp_path, conn, "src/billing", existing_only=False)
    assert "src/billing" in sym_exact

    # 3. code_graph & sync edge cases
    assert _normalize_rel_path("./src/a.py") == "src/a.py"
    assert _find_import_line("", "foo") == 1
    assert _is_governed_source_file("src\\mod.py") is True
    store_code_symbols(conn, "src/billing/a.py", ["CNT-BILL"], [], imports=["os"])
    resolver = _CodeOwnershipResolver(conn)
    missing_file = tmp_path / "src" / "billing" / "missing.py"
    index_source_file(conn, get_ast_indexer(), missing_file, "src/billing/missing.py", 0.0, "sha", resolver)
    conn.close()

    # check_index_dirty when eidos_dir is missing + reconcile_code_ownership self-healing a code/test file
    eidos_dir = tmp_path / "repo_sync" / "eidos"
    (eidos_dir / "architecture" / "containers").mkdir(parents=True)
    src_dir = tmp_path / "repo_sync" / "src" / "app"
    src_dir.mkdir(parents=True)
    test_cs = src_dir / "OrderTests.cs"
    test_cs.write_text("public class OrderTests { [Fact] public void TestOne() {} }\n", encoding="utf-8")
    (eidos_dir / "architecture" / "containers" / "app.adoc").write_text(
        "---\ntype: tech/container\nid: CNT-APP\ntitle: App\nstatus: active\ncontainer_type: library\nruntime: .NET\nsource_path: src/app\n---\n= App\n",
        encoding="utf-8",
    )
    idx = ArchitecturalIndex(
        db_path=eidos_dir / ".cache" / "index.db", repo_root=tmp_path / "repo_sync", eidos_dir=eidos_dir
    )
    idx.sync(force=True, auto_semantic=False)
    c2 = idx._get_connection()
    c2.execute(
        "INSERT OR REPLACE INTO indexed_files (rel_path, doc_type, mtime, sha256) VALUES ('src/app/OrderTests.cs', 'code/source', 0.0, 'old');"
    )
    c2.commit()
    reconcile_code_ownership(idx, c2, get_ast_indexer(), _CodeOwnershipResolver(c2), set())
    row_after = c2.execute("SELECT doc_type FROM indexed_files WHERE rel_path = 'src/app/OrderTests.cs';").fetchone()
    assert row_after is not None and row_after[0] == "code/test"
    idx.close()

    # Missing eidos_dir triggers deleted-files branch in check_index_dirty
    ghost_eidos = tmp_path / "ghost" / "eidos"
    ghost_eidos.mkdir(parents=True)
    idx_ghost = ArchitecturalIndex(
        db_path=ghost_eidos / ".cache" / "index.db", repo_root=tmp_path / "ghost", eidos_dir=ghost_eidos
    )
    idx_ghost._get_connection().execute(
        "INSERT OR REPLACE INTO indexed_files (rel_path, doc_type, mtime, sha256) VALUES ('system.adoc', 'product/system', 0.0, 'x');"
    )
    idx_ghost._get_connection().commit()
    idx_ghost.eidos_dir = tmp_path / "ghost" / "nonexistent_eidos"
    is_d, _mod, dels = check_index_dirty(idx_ghost)
    assert is_d is True and dels
    idx_ghost.close()

    # 4. ast_imports non-supported extension & missing file fallback
    indexer = get_ast_indexer()
    assert indexer.extract_declared_namespaces("src/readme.txt", "hello") == []
    assert indexer.extract_declared_namespaces(tmp_path / "does_not_exist.cs") == []
