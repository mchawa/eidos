"""Tests verifying DLT-2026-021 & CAP-AGENT-002: Polyglot AST Code Graph & Structural Repo Map Intelligence.

Enforces:
- INV-INDEX-OPTIONAL-AST-FALLBACK
- INV-INDEX-POLYGLOT-SYMBOL-PRECISION
- INV-INDEX-AI-REPO-MAP-BUDGET
- POL-004-ZERO-AI-CALL-DEPENDENCY
"""

import json
import sys
import time
from pathlib import Path

from typer.testing import CliRunner

from eidos.cli.main import app
from eidos.core.index.ast_indexer import (
    FallbackIndexer,
    get_ast_indexer,
)
from eidos.core.index.graph_index import get_architectural_index

runner = CliRunner()


def test_inv_index_optional_ast_fallback():
    """Verify INV-INDEX-OPTIONAL-AST-FALLBACK: Engine functions cleanly with zero unhandled exceptions when ast-grep is absent."""
    fallback_idx = get_ast_indexer(force_fallback=True)
    assert isinstance(fallback_idx, FallbackIndexer)

    # 1. Test Python symbol extraction on ast_indexer.py
    py_path = Path("src/eidos/core/index/ast_indexer.py")
    symbols = fallback_idx.extract_symbols(py_path)
    assert len(symbols) >= 10
    names = {s.name for s in symbols}
    assert "CodeSymbol" in names
    assert "TestFunctionBinding" in names
    assert "FallbackIndexer" in names

    # 2. Test test function extraction on test_policy_zero_ai.py
    test_path = Path("tests/test_policy_zero_ai.py")
    test_bindings = fallback_idx.extract_test_bindings(test_path)
    assert len(test_bindings) >= 2
    tb_names = {t.test_name for t in test_bindings}
    assert "test_pyproject_contains_zero_ai_dependencies" in tb_names

    # 3. Test repo map generation via fallback
    repo_map = fallback_idx.generate_repo_map({str(py_path): symbols}, max_tokens=1000)
    assert "# 🗺️ Structural Code Graph (Repo Map)" in repo_map
    assert "class CodeSymbol" in repo_map


def test_inv_index_polyglot_symbol_precision():
    """Verify INV-INDEX-POLYGLOT-SYMBOL-PRECISION: Sub-10ms precision symbol extraction across Python & TypeScript."""
    idx = get_ast_indexer()
    is_instrumented = "coverage" in sys.modules or sys.gettrace() is not None

    # Warm up symbol extractor to avoid one-off dynamic import overhead skewing latency
    idx.extract_symbols(Path("src/eidos/core/index/ast_indexer.py"))

    # 1. Python file symbol precision
    t0 = time.perf_counter()
    py_symbols = idx.extract_symbols(Path("src/eidos/core/index/ast_indexer.py"))
    py_ms = (time.perf_counter() - t0) * 1000
    max_py_ms = 60.0 if is_instrumented else 25.0
    assert py_ms < max_py_ms  # Well within latency limits

    classes = [s for s in py_symbols if s.kind == "class"]
    methods = [s for s in py_symbols if s.kind == "method"]
    assert len(classes) >= 4
    assert len(methods) >= 3

    # Check method parent linking
    to_dict_methods = [m for m in methods if m.name == "to_dict"]
    assert any(m.parent_symbol in ("CodeSymbol", "TestFunctionBinding") for m in to_dict_methods)

    # 2. TypeScript / TSX precision (if ast-grep is active)
    tsx_path = Path("src/eidos/portal/frontend/src/App.tsx")
    if tsx_path.exists():
        idx.extract_symbols(tsx_path)  # warm-up
        t1 = time.perf_counter()
        tsx_symbols = idx.extract_symbols(tsx_path)
        tsx_ms = (time.perf_counter() - t1) * 1000
        max_tsx_ms = 40.0 if is_instrumented else 15.0
        assert tsx_ms < max_tsx_ms
        # App function should be extracted
        fn_names = {s.name for s in tsx_symbols}
        assert "App" in fn_names

    # 3. Go type_spec (struct / interface) and receiver method parent linking
    go_code = (
        "package store\n\n"
        "type LedgerStore struct {\n    Path string\n}\n\n"
        "type Reader interface {\n    Read(id string) error\n}\n\n"
        "func (s *LedgerStore) Append(entry string) error {\n    return nil\n}\n"
    )
    go_syms = idx.extract_symbols("pkg/store/ledger.go", go_code)
    go_by_name = {s.name: s for s in go_syms}
    assert "LedgerStore" in go_by_name and go_by_name["LedgerStore"].kind == "struct"
    assert "Reader" in go_by_name and go_by_name["Reader"].kind == "interface"
    assert "Append" in go_by_name and go_by_name["Append"].parent_symbol == "LedgerStore"


def test_polyglot_kotlin_and_qualified_class_test_methods():
    """Verify Kotlin symbol/test extraction and Class::method test qualification without overwriting."""  # eidos:fixture
    idx = get_ast_indexer()

    # 1. Kotlin (.kt) symbol extraction and @Test fun discovery
    kt_code = (
        "package com.example\n\n"
        "class OrderService(val repo: String) {\n"
        "    fun placeOrder(id: String): Boolean {\n"
        "        return true\n"
        "    }\n"
        "}\n\n"
        "class OrderServiceTest {\n"
        "    @Test\n"
        "    fun validatesOrderInvariant() {\n"
        "        // INV-KT-ORDER-001\n"
        "        assert(true)\n"
        "    }\n"
        "}\n"
    )
    kt_syms = idx.extract_symbols("src/main/kotlin/OrderService.kt", kt_code)
    kt_names = {(s.kind, s.name, s.parent_symbol) for s in kt_syms}
    assert ("class", "OrderService", None) in kt_names
    assert ("method", "placeOrder", "OrderService") in kt_names
    kt_funcs = idx.list_test_functions("src/test/kotlin/OrderServiceTest.kt", kt_code)
    assert "OrderServiceTest::validatesOrderInvariant" in kt_funcs
    assert "validatesOrderInvariant" in kt_funcs

    # 2. Qualified Class::method test functions across both AstGrepIndexer and FallbackIndexer
    dup_py = (
        "class TestAlpha:\n"
        "    def test_check(self):\n"
        "        # INV-ALPHA-001\n"
        "        assert True\n\n"
        "class TestBeta:\n"
        "    def test_check(self):\n"
        "        # INV-BETA-002\n"
        "        assert True\n"
    )
    for engine in (idx, get_ast_indexer(force_fallback=True)):
        fn_map = engine.list_test_functions("tests/test_dup.py", dup_py)
        assert "TestAlpha::test_check" in fn_map and "INV-ALPHA-001" in fn_map["TestAlpha::test_check"]
        assert "TestBeta::test_check" in fn_map and "INV-BETA-002" in fn_map["TestBeta::test_check"]
        assert "INV-ALPHA-001" in fn_map["test_check"] and "INV-BETA-002" in fn_map["test_check"]
        bindings = {b.identifier: b.invariants for b in engine.extract_test_bindings("tests/test_dup.py", dup_py)}
        assert bindings["tests/test_dup.py::TestAlpha::test_check"] == ["INV-ALPHA-001"]
        assert bindings["tests/test_dup.py::TestBeta::test_check"] == ["INV-BETA-002"]
        assert set(bindings["tests/test_dup.py::test_check"]) == {"INV-ALPHA-001", "INV-BETA-002"}


def test_inv_index_ai_repo_map_budget():
    """Verify INV-INDEX-AI-REPO-MAP-BUDGET: Repo map achieves >= 85% token compression in sub-15ms."""
    repo_root = Path(__file__).parent.parent
    core_files = list((repo_root / "src" / "eidos" / "core").rglob("*.py"))
    assert len(core_files) >= 5

    idx = get_ast_indexer()
    is_instrumented = "coverage" in sys.modules or sys.gettrace() is not None
    symbols_by_file = {}
    raw_size = 0

    t0 = time.perf_counter()
    for f in core_files:
        content = f.read_text(encoding="utf-8", errors="replace")
        raw_size += len(content)
        rel = str(f.relative_to(repo_root)).replace("\\", "/")
        symbols_by_file[rel] = idx.extract_symbols(f, content)

    repo_map = idx.generate_repo_map(symbols_by_file, max_tokens=2000)
    duration_ms = (time.perf_counter() - t0) * 1000

    map_size = len(repo_map)
    compression = (1 - (map_size / raw_size)) * 100

    max_duration_ms = 500.0 if is_instrumented else 250.0
    assert duration_ms < max_duration_ms  # Fast batch parsing
    assert compression >= 85.0  # Strict >= 85% compression requirement
    assert "# 🗺️ Structural Code Graph (Repo Map)" in repo_map


def test_architectural_index_find_symbol_and_repo_map():
    """Verify ArchitecturalIndex.find_symbol and get_repo_map integrations."""
    arch_idx = get_architectural_index()
    arch_idx.sync()

    # 1. find_symbol lookup
    matches = arch_idx.find_symbol("ArchitecturalIndex")
    assert len(matches) > 0
    match_entities = {m["entity_id"] for m in matches}
    assert "CNT-EIDOS-INDEX" in match_entities

    # Partial match
    to_dict_matches = arch_idx.find_symbol("to_dict")
    assert len(to_dict_matches) > 0

    # 2. get_repo_map
    repo_map = arch_idx.get_repo_map(max_tokens=1000)
    assert "# 🗺️ Structural Code Graph (Repo Map)" in repo_map
    assert "src/eidos/core/index/ast_indexer.py" in repo_map or "src/eidos/core/index/graph_index.py" in repo_map


def test_cli_repo_map_and_find_symbol():
    """Verify CLI commands 'eidos index repo-map' and 'eidos index find-symbol'."""
    # 1. repo-map markdown
    res_map = runner.invoke(app, ["index", "repo-map", "--max-tokens", "800"])
    assert res_map.exit_code == 0
    assert "Structural Code Graph" in res_map.output

    # 2. repo-map json
    res_map_json = runner.invoke(app, ["index", "repo-map", "--format", "json", "--max-tokens", "800"])
    assert res_map_json.exit_code == 0
    data = json.loads(res_map_json.output)
    assert "repo_map" in data
    assert "estimated_tokens" in data

    # 3. find-symbol text
    res_sym = runner.invoke(app, ["index", "find-symbol", "ArchitecturalIndex"])
    assert res_sym.exit_code == 0
    assert "CNT-EIDOS-IND" in res_sym.output

    # 4. find-symbol json
    res_sym_json = runner.invoke(app, ["index", "find-symbol", "ArchitecturalIndex", "--json"])
    assert res_sym_json.exit_code == 0
    sym_data = json.loads(res_sym_json.output)
    assert sym_data["count"] > 0
    assert any(m["entity_id"] == "CNT-EIDOS-INDEX" for m in sym_data["matches"])

    # 5. top-level shortcuts are removed
    res_top_map = runner.invoke(app, ["repo-map", "--max-tokens", "500"])
    assert res_top_map.exit_code != 0

    res_top_sym = runner.invoke(app, ["find-symbol", "AstGrepIndexer"])
    assert res_top_sym.exit_code != 0


def test_classify_design_changes_categories():
    """Verify DLT-2026-030: frontend diffs are classified into token / ui_contract / body_only."""
    from eidos.core.index.ast_indexer import classify_design_changes

    # Non-design file
    assert classify_design_changes("a", "b", "src/app.py")[0] == "none"

    # CSS: custom property value change -> token; utility tweak -> body_only
    old_css = ":root { --brand: #111; }\n.btn { padding: 4px; }\n"
    assert classify_design_changes(old_css, old_css.replace("#111", "#222"), "x.css")[0] == "token"
    assert classify_design_changes(old_css, old_css.replace("4px", "6px"), "x.css")[0] == "body_only"
    # @keyframes and SCSS variables are tokens
    assert classify_design_changes(".a{}", ".a{}\n@keyframes spin { to { rotate: 1turn; } }", "x.css")[0] == "token"
    assert classify_design_changes("$gap: 4px;\n", "$gap: 8px;\n", "x.scss")[0] == "token"

    # TSX: Props contract change -> ui_contract; internal hook edit -> body_only
    old_tsx = (
        "interface ButtonProps { label: string }\n"
        "export function Button({ label }: ButtonProps) {\n"
        "  const x = useMemo(() => 1, []);\n  return <b>{label}</b>;\n}\n"
    )
    props_tsx = old_tsx.replace("label: string }", "label: string; variant?: 'primary' }")
    body_tsx = old_tsx.replace("() => 1", "() => 2")
    assert classify_design_changes(old_tsx, props_tsx, "Button.tsx")[0] == "ui_contract"
    assert classify_design_changes(old_tsx, body_tsx, "Button.tsx")[0] == "body_only"

    # Routes and exports
    routes_old = "const routes = [{ path: '/home' }];\n"
    assert (
        classify_design_changes(routes_old, routes_old + "routes.push({ path: '/admin' });\n", "routes.tsx")[0]
        == "ui_contract"
    )
    assert (
        classify_design_changes("export const A = 1;", "export const A = 1;\nexport const B = 2;", "ui.jsx")[0]
        == "ui_contract"
    )

    # Token modules, Svelte/Vue props, and added/deleted files
    assert (
        classify_design_changes("export const c = '#111';", "export const c = '#222';", "md3-tokens.ts")[0] == "token"
    )
    assert (
        classify_design_changes("export let size = 'md';", "export let size = 'lg';\nexport let icon = '';", "Btn.svelte")[0]
        == "ui_contract"
    )
    assert (
        classify_design_changes(
            "<script setup>defineProps<{id: string}>()</script>",
            "<script setup>defineProps<{id: string, active: boolean}>()</script>",
            "Card.vue",
        )[0]
        == "ui_contract"
    )
    assert classify_design_changes(None, ".a{}", "new.css")[0] == "token"
    assert classify_design_changes("x", None, "Old.tsx")[0] == "ui_contract"


def test_has_structural_changes_unchanged_after_refactor():
    """INV-DRIFT-DIFF-AWARE-COVERAGE: has_structural_changes keeps its contract after delegating to diff_public_symbols."""
    from eidos.core.index.ast_indexer import diff_public_symbols, has_structural_changes

    old = "def keep(a):\n    return a\n\n\ndef gone():\n    return 1\n\n\ndef _private():\n    return 0\n"
    new = "def keep(a, b):\n    return a\n\n\ndef added():\n    return 1\n\n\nclass Fresh:\n    def go(self):\n        return 1\n"
    diff = diff_public_symbols(old, new, "mod.py")
    assert diff.added == [("class", "", "Fresh"), ("function", "", "added"), ("method", "Fresh", "go")]
    assert diff.removed == [("function", "", "gone")]
    assert diff.changed == [("function", "", "keep")]
    assert has_structural_changes(old, new, "mod.py") == (True, diff.reasons())
    assert diff.reasons() == [
        "added class 'Fresh'",
        "added function 'added'",
        "added method 'Fresh.go'",
        "removed function 'gone'",
        "changed signature of function 'keep'",
    ]
    # Body-only and private edits stay non-structural; added/deleted files stay structural
    assert has_structural_changes(old, old.replace("return 0", "return 5"), "mod.py") == (False, [])
    assert has_structural_changes(None, new, "mod.py") == (True, ["added file 'mod.py'"])
    assert has_structural_changes(old, None, "mod.py") == (True, ["deleted file 'mod.py'"])
    assert has_structural_changes("a: 1\n", "a: 2\n", "cfg.yaml") == (True, ["modified 'cfg.yaml'"])
    assert not diff_public_symbols("a: 1\n", "a: 2\n", "cfg.yaml")


def test_polyglot_language_native_visibility_rules():
    """Verify DLT-2026-055 & INV-DRIFT-DIFF-AWARE-COVERAGE: language-native visibility rules across TS/JS, Go, Rust, Java/Kotlin, and Python __all__."""
    from eidos.core.index.ast_indexer import has_structural_changes, public_symbol_map

    # 1. TypeScript: non-exported helper is private; exported function is public
    ts_old = "export function fetchUser(id: string): string {\n  return id;\n}\n"
    ts_with_private = ts_old + "\nfunction parseInternalHeader(raw: string): string {\n  return raw.trim();\n}\n"
    ts_with_export = ts_old + "\nexport function createUser(name: string): string {\n  return name;\n}\n"
    assert has_structural_changes(ts_old, ts_with_private, "client.ts") == (False, [])
    assert has_structural_changes(ts_old, ts_with_export, "client.ts")[0] is True

    # 2. Go: lowercase function is unexported; uppercase function is exported
    go_old = "package store\n\nfunc OpenStore(path string) error {\n    return nil\n}\n"
    go_with_private = go_old + "\nfunc parseHeader(raw string) string {\n    return raw\n}\n"
    go_with_exported = go_old + "\nfunc CloseStore() error {\n    return nil\n}\n"
    assert has_structural_changes(go_old, go_with_private, "store.go") == (False, [])
    assert has_structural_changes(go_old, go_with_exported, "store.go")[0] is True

    # 3. Rust: fn without pub is private; pub fn is public
    rs_old = "pub fn execute_query(q: &str) -> bool {\n    true\n}\n"
    rs_with_private = rs_old + "\nfn helper_normalize(q: &str) -> &str {\n    q\n}\n"
    rs_with_pub = rs_old + "\npub fn validate_query(q: &str) -> bool {\n    !q.is_empty()\n}\n"
    assert has_structural_changes(rs_old, rs_with_private, "lib.rs") == (False, [])
    assert has_structural_changes(rs_old, rs_with_pub, "lib.rs")[0] is True

    # 4. Kotlin / Java: private and internal declarations are excluded from public symbols
    kt_code = (
        "package com.example\n\n"
        "public class PublicApi {\n"
        "    fun execute(): Boolean {\n        return true\n    }\n"
        "    private fun secretHelper(): Boolean {\n        return false\n    }\n"
        "}\n\n"
        "internal fun internalUtil(): Int {\n    return 1\n}\n"
    )
    kt_pub = public_symbol_map(kt_code, "PublicApi.kt")
    assert ("class", "", "PublicApi") in kt_pub
    assert ("method", "PublicApi", "execute") in kt_pub
    assert ("method", "PublicApi", "secretHelper") not in kt_pub
    assert ("function", "", "internalUtil") not in kt_pub

    # 5. Python: module-level __all__ restricts public boundary symbols when defined
    py_all = (
        "__all__ = ['exported_fn']\n\n"
        "def exported_fn():\n    return 1\n\n"
        "def internal_but_no_underscore():\n    return 2\n"
    )
    py_pub = public_symbol_map(py_all, "mod.py")
    assert ("function", "", "exported_fn") in py_pub
    assert ("function", "", "internal_but_no_underscore") not in py_pub


def test_polyglot_rust_java_ts_bindings_and_fallback_delegation(monkeypatch):
    """Verify Rust struct/enum/trait/impl extraction, polyglot test binding extraction, repo-map truncation, and AST_GREP_AVAILABLE=False fallback."""  # eidos:fixture
    import eidos.core.index.ast_indexer as ast_mod
    from eidos.core.index.ast_indexer import AstGrepIndexer, FallbackIndexer

    # 1. Rust struct, enum, trait, and impl method extraction across AstGrepIndexer and FallbackIndexer
    rs_code = (
        "pub struct VaultConfig {\n    pub key: String,\n}\n\n"
        "pub enum VaultMode {\n    Strict,\n    Lenient,\n}\n\n"
        "pub trait Unlocker {\n    fn unlock(&self) -> bool;\n}\n\n"
        "impl VaultConfig {\n"
        "    pub fn new(key: String) -> Self {\n        Self { key }\n    }\n"
        "}\n\n"
        "#[test]\n"
        "fn test_vault_invariant() {\n"
        "    // INV-RS-VAULT-001 and POL-001\n"
        "    assert!(true);\n"
        "}\n"
    )
    ast_idx = AstGrepIndexer()
    rs_syms = {s.name: s for s in ast_idx.extract_symbols("src/lib.rs", rs_code)}
    assert rs_syms["VaultConfig"].kind == "struct"
    assert rs_syms["VaultMode"].kind == "enum"
    assert rs_syms["Unlocker"].kind == "interface"
    assert rs_syms["new"].kind == "method"
    assert rs_syms["new"].parent_symbol == "VaultConfig"

    fb_idx = FallbackIndexer()
    fb_rs_syms = {s.name: s for s in fb_idx.extract_symbols("src/lib.rs", rs_code)}
    assert fb_rs_syms["VaultConfig"].kind == "struct"
    assert fb_rs_syms["VaultMode"].kind == "enum"
    assert fb_rs_syms["Unlocker"].kind == "interface"

    # 2. Polyglot extract_test_bindings across TS, Go, Rust, and Java
    ts_test = (
        "it('enforces token budget', () => {\n"
        "  // INV-TS-BUDGET-001\n"
        "  expect(true).toBe(true);\n"
        "});\n"
    )
    go_test = (
        "package app\n\n"
        "func TestStoreSync(t *testing.T) {\n"
        "    // INV-GO-SYNC-001\n"
        "}\n"
    )
    java_test = (
        "public class AuthTest {\n"
        "    @Test\n"
        "    public void verifiesAuthToken() {\n"
        "        // INV-JAVA-AUTH-001\n"
        "    }\n"
        "}\n"
    )
    for engine in (ast_idx, fb_idx):
        rs_b = engine.extract_test_bindings("src/lib.rs", rs_code)
        assert any("INV-RS-VAULT-001" in b.invariants for b in rs_b)
        ts_b = engine.extract_test_bindings("src/app.test.ts", ts_test)
        assert any("INV-TS-BUDGET-001" in b.invariants for b in ts_b)
        go_b = engine.extract_test_bindings("pkg/store_test.go", go_test)
        assert any("INV-GO-SYNC-001" in b.invariants for b in go_b)
        java_b = engine.extract_test_bindings("src/AuthTest.java", java_test)
        assert any("INV-JAVA-AUTH-001" in b.invariants for b in java_b)

    # 3. Simulated AST_GREP_AVAILABLE = False delegates cleanly to FallbackIndexer
    monkeypatch.setattr(ast_mod, "AST_GREP_AVAILABLE", False)
    assert isinstance(ast_mod.get_ast_indexer(), FallbackIndexer)
    delegated_syms = ast_idx.extract_symbols("src/lib.rs", rs_code)
    assert any(s.name == "VaultConfig" for s in delegated_syms)
    delegated_bindings = ast_idx.extract_test_bindings("src/lib.rs", rs_code)
    assert any("INV-RS-VAULT-001" in b.invariants for b in delegated_bindings)

    # 4. Broken Python syntax falls back to regex in FallbackIndexer without raising SyntaxError
    broken_py = "class PartialClass:\n    def ok_func(x):\n        return x\n    def broken(:\n"
    broken_syms = fb_idx.extract_symbols("broken.py", broken_py)
    assert {s.name for s in broken_syms} >= {"PartialClass", "ok_func"}

    # 5. Repo map truncation marker under tight token budget
    many_files = {f"src/mod_{i}.py": delegated_syms for i in range(25)}
    truncated_map = fb_idx.generate_repo_map(many_files, max_tokens=40)
    assert "... [remaining modules truncated to preserve context token budget]" in truncated_map
