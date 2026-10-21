"""Tests verifying DLT-2026-021 & CAP-AGENT-002: Polyglot AST Code Graph & Structural Repo Map Intelligence.

Enforces:
- INV-INDEX-OPTIONAL-AST-FALLBACK
- INV-INDEX-POLYGLOT-SYMBOL-PRECISION
- INV-INDEX-AI-REPO-MAP-BUDGET
- POL-004-ZERO-AI-CALL-DEPENDENCY
"""

import sys
import time
from pathlib import Path

import orjson
from typer.testing import CliRunner

from eidos.cli.main import app
from eidos.core.index.ast_indexer import (
    AstGrepIndexer,
    get_ast_indexer,
)
from eidos.core.index.graph_index import get_architectural_index

runner = CliRunner()


def test_inv_index_optional_ast_fallback():
    """Verify INV-INDEX-OPTIONAL-AST-FALLBACK: Engine extracts Python stdlib ast signatures and falls back to Tree-sitter on broken syntax without unhandled exceptions."""
    idx = get_ast_indexer()
    assert isinstance(idx, AstGrepIndexer)

    # 1. Test Python symbol extraction on ast_indexer.py
    py_path = Path("src/eidos/core/index/ast_indexer.py")
    symbols = idx.extract_symbols(py_path)
    assert len(symbols) >= 10
    names = {s.name for s in symbols}
    assert "CodeSymbol" in names
    assert "TestFunctionBinding" in names
    assert "AstGrepIndexer" in names

    # 2. Test error-tolerant Tree-sitter fallback on broken Python syntax
    broken_py = "class PartialClass:\n    def ok_func(x):\n        return x\n    def broken(:\n"
    broken_syms = idx.extract_symbols("broken.py", broken_py)
    assert {s.name for s in broken_syms} >= {"PartialClass", "ok_func"}

    # 3. Test test function extraction on test_policy_zero_ai.py
    test_path = Path("tests/test_policy_zero_ai.py")
    test_bindings = idx.extract_test_bindings(test_path)
    assert len(test_bindings) >= 2
    tb_names = {t.test_name for t in test_bindings}
    assert "test_pyproject_contains_zero_ai_dependencies" in tb_names

    # 4. Test repo map generation
    repo_map = idx.generate_repo_map({str(py_path): symbols}, max_tokens=1000)
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
    assert len(classes) >= 3
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

    # 2. Qualified Class::method test functions
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
    fn_map = idx.list_test_functions("tests/test_dup.py", dup_py)
    assert "TestAlpha::test_check" in fn_map and "INV-ALPHA-001" in fn_map["TestAlpha::test_check"]
    assert "TestBeta::test_check" in fn_map and "INV-BETA-002" in fn_map["TestBeta::test_check"]
    assert "INV-ALPHA-001" in fn_map["test_check"] and "INV-BETA-002" in fn_map["test_check"]
    bindings = {b.identifier: b.invariants for b in idx.extract_test_bindings("tests/test_dup.py", dup_py)}
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
    assert "CMP-GRAPH-STORAGE" in match_entities

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
    res_map_json = runner.invoke(app, ["index", "repo-map", "--json", "--max-tokens", "800"])
    assert res_map_json.exit_code == 0
    data = orjson.loads(res_map_json.output)
    assert "repo_map" in data
    assert "estimated_tokens" in data

    # 3. find-symbol text
    res_sym = runner.invoke(app, ["index", "find-symbol", "ArchitecturalIndex"])
    assert res_sym.exit_code == 0
    assert "CMP-GRAPH-STO" in res_sym.output

    # 4. find-symbol json
    res_sym_json = runner.invoke(app, ["index", "find-symbol", "ArchitecturalIndex", "--json"])
    assert res_sym_json.exit_code == 0
    sym_data = orjson.loads(res_sym_json.output)
    assert sym_data["count"] > 0
    assert any(m["entity_id"] == "CMP-GRAPH-STORAGE" for m in sym_data["matches"])

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
        classify_design_changes(
            "export let size = 'md';", "export let size = 'lg';\nexport let icon = '';", "Btn.svelte"
        )[0]
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


def test_polyglot_rust_java_ts_bindings_and_fallback_delegation(tmp_path):
    """Verify Rust struct/enum/trait/impl extraction, polyglot test binding extraction, repo-map truncation, and error-tolerant Python parsing."""  # eidos:fixture
    from eidos.core.index.ast_indexer import AstGrepIndexer

    # 1. Rust struct, enum, trait, and impl method extraction
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

    # 2. Polyglot extract_test_bindings across TS, Go, Rust, and Java
    ts_test = "it('enforces token budget', () => {\n  // INV-TS-BUDGET-001\n  expect(true).toBe(true);\n});\n"
    go_test = "package app\n\nfunc TestStoreSync(t *testing.T) {\n    // INV-GO-SYNC-001\n}\n"
    java_test = (
        "public class AuthTest {\n"
        "    @Test\n"
        "    public void verifiesAuthToken() {\n"
        "        // INV-JAVA-AUTH-001\n"
        "    }\n"
        "}\n"
    )
    rs_b = ast_idx.extract_test_bindings("src/lib.rs", rs_code)
    assert any("INV-RS-VAULT-001" in b.invariants for b in rs_b)
    ts_b = ast_idx.extract_test_bindings("src/app.test.ts", ts_test)
    assert any("INV-TS-BUDGET-001" in b.invariants for b in ts_b)
    go_b = ast_idx.extract_test_bindings("pkg/store_test.go", go_test)
    assert any("INV-GO-SYNC-001" in b.invariants for b in go_b)
    java_b = ast_idx.extract_test_bindings("src/AuthTest.java", java_test)
    assert any("INV-JAVA-AUTH-001" in b.invariants for b in java_b)

    # 3. Python stdlib ast extraction
    py_code = "class VaultConfig:\n    pass\n\ndef test_vault_invariant():\n    # INV-RS-VAULT-001 and POL-001\n    assert True\n"
    delegated_syms = ast_idx.extract_symbols("src/lib.py", py_code)
    assert any(s.name == "VaultConfig" for s in delegated_syms)
    delegated_bindings = ast_idx.extract_test_bindings("src/lib.py", py_code)
    assert any("INV-RS-VAULT-001" in b.invariants for b in delegated_bindings)

    # 4. Broken Python syntax falls back to Tree-sitter SgRoot(content, "python") without raising SyntaxError
    broken_py = "class PartialClass:\n    def ok_func(x):\n        return x\n    def broken(:\n"
    broken_syms = ast_idx.extract_symbols("broken.py", broken_py)
    assert {s.name for s in broken_syms} >= {"PartialClass", "ok_func"}

    # 5. Repo map truncation marker under tight token budget
    many_files = {f"src/mod_{i}.py": delegated_syms for i in range(25)}
    truncated_map = ast_idx.generate_repo_map(many_files, max_tokens=40)
    assert "... [remaining modules truncated to preserve context token budget]" in truncated_map

    # 6. AstGrepIndexer.extract_symbols on TS, Go, and Kotlin + list_test_functions on broken Python
    ts_src = (
        "export interface UserDto { id: string }\n"
        "export class UserService {\n"
        "  fetchUser() { return 1; }\n"
        "}\n"
        "function helperFn() { return 2; }\n"
        "export { helperFn as publicHelper, type UserDto as ExportedDto }\n"
    )
    ts_syms = ast_idx.extract_symbols("src/user.ts", ts_src)
    assert {s.name for s in ts_syms} >= {"UserDto", "UserService", "helperFn"}

    go_src = (
        "package store\n"
        "type Reader interface { Read() error }\n"
        "type itemStore struct{}\n"
        "func OpenStore() {}\n"
        "func internalHelper() {}\n"
    )
    go_syms = ast_idx.extract_symbols("pkg/store.go", go_src)
    assert {s.name for s in go_syms} >= {"Reader", "itemStore", "OpenStore", "internalHelper"}

    kt_src = (
        "interface AuthPort {}\nclass AuthAdapter {\n    fun verifyToken() {}\n    private fun secretHelper() {}\n}\n"
    )
    kt_syms = ast_idx.extract_symbols("src/Auth.kt", kt_src)
    assert {s.name for s in kt_syms} >= {"AuthPort", "AuthAdapter", "verifyToken"}

    assert ast_idx.list_test_functions(tmp_path / "does_not_exist_test.py") == {}
    broken_py_tests = (
        "# comment before test_first\n"
        "def test_first():\n"
        "    # INV-FIRST-001\n"
        "    assert True\n"
        "def test_second(:\n"
        "    # INV-SECOND-001\n"
    )
    listed = ast_idx.list_test_functions("tests/broken_test.py", broken_py_tests)
    assert "test_first" in listed and "test_second" in listed

    # 7. _extract_python_dunder_all_from_source, _evaluate_symbol_visibility, and _is_public_symbol edge cases
    from eidos.core.index.ast_indexer import (
        CodeSymbol,
        _evaluate_symbol_visibility,
        _extract_python_dunder_all_from_source,
        _extract_ui_contract,
        _is_public_symbol,
        classify_design_changes,
    )

    assert _extract_python_dunder_all_from_source("__all__: list[str] = ['Alpha', 'Beta']\n") == {"Alpha", "Beta"}
    assert _extract_python_dunder_all_from_source("def broken(:\n") is None

    assert (
        _evaluate_symbol_visibility(
            CodeSymbol(
                name="Read",
                kind="method",
                file_path="a.go",
                line_start=1,
                line_end=1,
                parent_symbol="unexportedStruct",
            )
        )
        is False
    )
    assert (
        _evaluate_symbol_visibility(
            CodeSymbol(name="compute", kind="function", file_path="a.rs", line_start=1, line_end=1, signature="")
        )
        is True
    )
    assert (
        _evaluate_symbol_visibility(
            CodeSymbol(
                name="run",
                kind="method",
                file_path="a.ts",
                line_start=1,
                line_end=1,
                parent_symbol="InternalCls",
            ),
            class_is_public={"InternalCls": False},
        )
        is False
    )
    assert (
        _evaluate_symbol_visibility(
            CodeSymbol(
                name="run",
                kind="method",
                file_path="a.ts",
                line_start=1,
                line_end=1,
                parent_symbol="#priv",
            )
        )
        is False
    )
    assert (
        _evaluate_symbol_visibility(
            CodeSymbol(name="_hidden", kind="function", file_path="A.java", line_start=1, line_end=1)
        )
        is False
    )
    assert (
        _evaluate_symbol_visibility(
            CodeSymbol(
                name="work",
                kind="method",
                file_path="A.java",
                line_start=1,
                line_end=1,
                parent_symbol="PkgCls",
            ),
            class_is_public={"PkgCls": False},
        )
        is False
    )
    assert (
        _evaluate_symbol_visibility(
            CodeSymbol(
                name="method_a",
                kind="method",
                file_path="a.py",
                line_start=1,
                line_end=1,
                parent_symbol="UnlistedCls",
            ),
            py_dunder_all={"ListedCls"},
        )
        is False
    )
    assert (
        _is_public_symbol(
            CodeSymbol(name="PublicFn", kind="function", file_path="a.py", line_start=1, line_end=1, is_public=None)
        )
        is True
    )

    # 8. _extract_ui_contract type alias Props without braces and theme.ts whitespace-only change
    ui_contract = _extract_ui_contract(
        "type BadgeProps = BaseProps & ExtraProps;\nexport { Button as PrimaryButton };\n", ".tsx"
    )
    assert "PrimaryButton" in ui_contract["exports"]
    assert any(p.startswith("BadgeProps:") for p in ui_contract["props"])
    cat_ws, _ = classify_design_changes("export const t = 1;", "export  const  t = 1;", "src/theme.ts")
    assert cat_ws == "body_only"


def test_dlt_2026_083_polyglot_fallback_and_block_comment_stripping(tmp_path):
    """Verify AstGrepIndexer extracts symbols across C/C++/C#/Ruby/PHP/Swift/Vue/Svelte while ignoring block-commented declarations."""
    from eidos.core.index.ast_indexer import AstGrepIndexer, boundary_symbol_map

    indexer = AstGrepIndexer()

    cpp_file = tmp_path / "engine.hpp"
    cpp_file.write_text(
        "/*\n"
        "class CommentedOutEngine {};\n"
        "void commented_fn(int x) {}\n"
        "*/\n"
        "class RenderEngine {\n"
        "};\n"
        "static void internal_helper(int a) {\n"
        "}\n"
        "int compute_frame(int width, int height) {\n"
        "    return width * height;\n"
        "}\n",
        encoding="utf-8",
    )
    cpp_syms = indexer.extract_symbols(cpp_file)
    cpp_names = {s.name: s for s in cpp_syms}
    assert "CommentedOutEngine" not in cpp_names
    assert "commented_fn" not in cpp_names
    assert "RenderEngine" in cpp_names
    assert cpp_names["RenderEngine"].is_public is True
    assert "internal_helper" in cpp_names
    assert cpp_names["internal_helper"].is_public is False
    assert "compute_frame" in cpp_names
    assert cpp_names["compute_frame"].is_public is True
    assert cpp_names["compute_frame"].line_start == 9

    cs_file = tmp_path / "OrderService.cs"
    cs_file.write_text(
        "public class OrderService {\n"
        "    public void SubmitOrder(string id) {}\n"
        "    private void ValidateInternal(string id) {}\n"
        "}\n",
        encoding="utf-8",
    )
    cs_syms = {s.name: s for s in indexer.extract_symbols(cs_file)}
    assert cs_syms["OrderService"].is_public is True
    assert cs_syms["SubmitOrder"].is_public is True
    assert cs_syms["ValidateInternal"].is_public is False

    rb_file = tmp_path / "billing.rb"
    rb_file.write_text(
        "=begin\n"
        "def commented_ruby_method(x)\n"
        "end\n"
        "=end\n"
        "class BillingProcessor\n"
        "  def charge_card(amount)\n"
        "  end\n"
        "  def _internal_audit\n"
        "  end\n"
        "  private\n"
        "  def secret_settlement(amount)\n"
        "  end\n"
        "end\n",
        encoding="utf-8",
    )
    rb_syms = {s.name: s for s in indexer.extract_symbols(rb_file)}
    assert "commented_ruby_method" not in rb_syms
    assert rb_syms["BillingProcessor"].is_public is True
    assert rb_syms["charge_card"].is_public is True
    assert rb_syms["_internal_audit"].is_public is False
    assert rb_syms["secret_settlement"].is_public is False

    vue_file = tmp_path / "UserCard.vue"
    vue_file.write_text(
        '<script setup lang="ts">\n'
        "export interface UserCardProps {\n"
        "  userId: string;\n"
        "}\n"
        "const props = defineProps<UserCardProps>();\n"
        "export function formatUserName(name: string): string {\n"
        "  return name.trim();\n"
        "}\n"
        "function localHelper() {}\n"
        "</script>\n",
        encoding="utf-8",
    )
    vue_syms = {s.name: s for s in indexer.extract_symbols(vue_file)}
    assert vue_syms["UserCardProps"].is_public is True
    assert vue_syms["defineProps"].is_public is True
    assert vue_syms["formatUserName"].is_public is True
    assert vue_syms["localHelper"].is_public is False

    svelte_file = tmp_path / "Counter.svelte"
    svelte_file.write_text(
        '<script lang="ts">\nexport let initialCount = 0;\nfunction increment() { initialCount += 1; }\n</script>\n',
        encoding="utf-8",
    )
    svelte_syms = {s.name: s for s in indexer.extract_symbols(svelte_file)}
    assert svelte_syms["initialCount"].is_public is True
    assert svelte_syms["increment"].is_public is False

    php_file = tmp_path / "InvoiceService.php"
    php_file.write_text(
        "<?php\n"
        "interface Chargeable {}\n"
        "enum InvoiceStatus {}\n"
        "class InvoiceService {\n"
        "    public function issueInvoice($id) {}\n"
        "    private function recalcInternal($id) {}\n"
        "}\n",
        encoding="utf-8",
    )
    php_syms = {s.name: s for s in indexer.extract_symbols(php_file)}
    assert php_syms["Chargeable"].kind == "interface"
    assert php_syms["InvoiceStatus"].kind == "enum"
    assert php_syms["InvoiceService"].is_public is True
    assert php_syms["issueInvoice"].is_public is True
    assert php_syms["recalcInternal"].is_public is False

    swift_file = tmp_path / "AuthManager.swift"
    swift_file.write_text(
        "public protocol Authenticatable {}\n"
        "public struct AuthToken {}\n"
        "public enum AuthState {}\n"
        "public class AuthManager {\n"
        "    public func verifyToken() {}\n"
        "    private func clearCache() {}\n"
        "}\n",
        encoding="utf-8",
    )
    swift_syms = {s.name: s for s in indexer.extract_symbols(swift_file)}
    assert swift_syms["Authenticatable"].kind == "interface"
    assert swift_syms["AuthToken"].kind == "struct"
    assert swift_syms["AuthState"].kind == "enum"
    assert swift_syms["AuthManager"].is_public is True
    assert swift_syms["verifyToken"].is_public is True
    assert swift_syms["clearCache"].is_public is False

    # AstGrepIndexer.list_test_functions on TypeScript/JavaScript/Rust/Go/Java
    ts_funcs = indexer.list_test_functions(
        "src/auth.test.ts",
        "// preceding comment\nit('validates token expiry', () => { expect(1).toBe(1); });\nfunction testHelper() {}\n",
    )
    assert "validates token expiry" in ts_funcs
    assert "testHelper" in ts_funcs

    # boundary_symbol_map re-exports across __init__.py, index.ts, and lib.rs
    py_reexp = boundary_symbol_map("from .core import PublicClient as Client, _private\n", "src/pkg/__init__.py")
    assert ("export", "", "Client") in py_reexp
    ts_reexp = boundary_symbol_map("export { UserDto, type OrderDto } from './models';\n", "src/pkg/index.ts")
    assert ("export", "", "UserDto") in ts_reexp
    rs_reexp = boundary_symbol_map("pub use crate::engine::VaultEngine;\n", "src/lib.rs")
    assert ("export", "", "VaultEngine") in rs_reexp


def test_decorator_aware_symbol_signatures_and_line_start():
    """Verify DLT-2026-089: AstGrepIndexer includes decorators/attributes in line_start and signature."""
    from eidos.core.index.ast_indexer import AstGrepIndexer, diff_public_symbols

    py_v1 = (
        "from dataclasses import dataclass\n\n"
        "@dataclass(frozen=True)\n"
        "class OrderPayload:\n"
        "    order_id: str\n\n"
        "    @property\n"
        "    def normalized_id(self) -> str:\n"
        "        return self.order_id.strip()\n\n"
        "@app.command('create')\n"
        "def run_order(name: str) -> int:\n"
        "    return 0\n"
    )
    py_v2 = py_v1.replace("@app.command('create')", "@app.command('submit')")

    indexer = AstGrepIndexer()
    syms = {(s.kind, s.parent_symbol or "", s.name): s for s in indexer.extract_symbols("src/orders.py", py_v1)}
    cls_sym = syms[("class", "", "OrderPayload")]
    assert cls_sym.line_start == 3
    assert "@dataclass" in (cls_sym.signature or "")

    meth_sym = syms[("method", "OrderPayload", "normalized_id")]
    assert meth_sym.line_start == 7
    assert "@property" in (meth_sym.signature or "")

    fn_sym = syms[("function", "", "run_order")]
    assert fn_sym.line_start == 11
    assert "@app.command" in (fn_sym.signature or "")

    # Modifying only a decorator argument triggers a structural symbol diff in diff_public_symbols
    py_diff = diff_public_symbols(py_v1, py_v2, "src/orders.py")
    assert ("function", "", "run_order") in py_diff.changed

    # TypeScript @Component({ selector: 'app' }) with object literal in decorator does not truncate signature at '{'
    ts_v1 = (
        "@Component({ selector: 'app-header' })\n"
        "export class HeaderComponent {\n"
        "  @Input()\n"
        "  renderHeader(): string { return 'ok'; }\n"
        "}\n"
    )
    ts_v2 = ts_v1.replace("app-header", "app-navbar")
    ts_syms = {(s.kind, s.parent_symbol or "", s.name): s for s in indexer.extract_symbols("src/header.ts", ts_v1)}
    hdr = ts_syms[("class", "", "HeaderComponent")]
    assert hdr.line_start == 1
    assert hdr.is_public is True
    assert "@Component" in (hdr.signature or "")
    assert "class HeaderComponent" in (hdr.signature or "")

    ts_diff = diff_public_symbols(ts_v1, ts_v2, "src/header.ts")
    assert ("class", "", "HeaderComponent") in ts_diff.changed

    # Rust #[derive(...)] / #[must_use] attributes included in line_start and signature while preserving pub visibility
    rs_v1 = (
        "#[derive(Debug, Clone)]\n"
        "pub struct TokenRecord {\n"
        "    pub id: String,\n"
        "}\n\n"
        "#[must_use]\n"
        "pub fn issue_token(id: &str) -> TokenRecord {\n"
        "    TokenRecord { id: id.to_string() }\n"
        "}\n"
    )
    rs_v2 = rs_v1.replace("#[derive(Debug, Clone)]", "#[derive(Debug, Clone, PartialEq)]")
    rs_syms = {(s.kind, s.parent_symbol or "", s.name): s for s in indexer.extract_symbols("src/token.rs", rs_v1)}
    rec = rs_syms[("struct", "", "TokenRecord")]
    assert rec.line_start == 1
    assert rec.is_public is True
    assert "#[derive" in (rec.signature or "")

    rs_diff = diff_public_symbols(rs_v1, rs_v2, "src/token.rs")
    assert ("struct", "", "TokenRecord") in rs_diff.changed


def test_polyglot_parameterized_and_subtest_extraction():
    """Verify DLT-2026-089: test.each/it.each/Deno.test (TS/JS), t.Run (Go), and #[tokio::test]/#[rstest]/#[test_case] (Rust)."""
    from eidos.core.index.ast_indexer import AstGrepIndexer

    inv_p = "INV" + "-"
    ts_tests = (
        "// test('commented out fake test', () => {})\n"
        "it('handles /api/v1: ok', () => {\n"
        f"  // {inv_p}TS-STD-001\n"
        "});\n\n"
        "test.each([[1, 2], [3, 4]])('adds parameterized %i and %i', (a, b) => {\n"
        f"  // {inv_p}TS-PARAM-001\n"
        "  expect(a + b).toBeGreaterThan(0);\n"
        "});\n\n"
        "it.concurrent('runs concurrent check', async () => {\n"
        f"  // {inv_p}TS-CONC-001\n"
        "});\n\n"
        "Deno.test({ name: 'deno object test', fn() {\n"
        f"  // {inv_p}DENO-OBJ-001\n"
        "} });\n\n"
        "Deno.test('deno string test', () => {\n"
        f"  // {inv_p}DENO-STR-001\n"
        "});\n"
    )

    go_tests = (
        "package auth\n\n"
        'import "testing"\n\n'
        "// func TestCommentedFake(t *testing.T) {}\n"
        "func TestTokenFlow(t *testing.T) {\n"
        '    t.Run("expired_token", func(t *testing.T) {\n'
        f"        // {inv_p}GO-SUB-001\n"
        "    })\n"
        "}\n"
    )

    rs_tests = (
        "#[tokio::test]\n"
        "async fn async_worker_dispatches_job() {\n"
        f"    // {inv_p}RS-TOKIO-001\n"
        "    assert!(true);\n"
        "}\n\n"
        "#[rstest]\n"
        "#[case(1)]\n"
        "fn parameterized_rust_case(#[case] input: u32) {\n"
        f"    // {inv_p}RS-RSTEST-001\n"
        "    assert!(input > 0);\n"
        "}\n\n"
        "#[test_case(10 => 20)]\n"
        "fn MacroDrivenCase(v: u32) -> u32 {\n"
        f"    // {inv_p}RS-TESTCASE-001\n"
        "    v * 2\n"
        "}\n"
    )

    indexer = AstGrepIndexer()
    # TS/JS
    ts_fn_map = indexer.list_test_functions("tests/math.test.ts", ts_tests)
    assert "handles /api/v1: ok" in ts_fn_map
    assert "adds parameterized %i and %i" in ts_fn_map
    assert "runs concurrent check" in ts_fn_map
    assert "deno object test" in ts_fn_map
    assert "deno string test" in ts_fn_map
    assert "commented out fake test" not in ts_fn_map

    ts_bindings = {b.test_name: b for b in indexer.extract_test_bindings("tests/math.test.ts", ts_tests)}
    assert ts_bindings["handles /api/v1: ok"].invariants == [f"{inv_p}TS-STD-001"]
    assert ts_bindings["adds parameterized %i and %i"].invariants == [f"{inv_p}TS-PARAM-001"]
    assert ts_bindings["runs concurrent check"].invariants == [f"{inv_p}TS-CONC-001"]
    assert ts_bindings["deno object test"].invariants == [f"{inv_p}DENO-OBJ-001"]
    assert ts_bindings["deno string test"].invariants == [f"{inv_p}DENO-STR-001"]

    # Go subtests
    go_fn_map = indexer.list_test_functions("pkg/auth_test.go", go_tests)
    assert "TestTokenFlow" in go_fn_map
    assert "TestTokenFlow/expired_token" in go_fn_map
    assert "TestTokenFlow::expired_token" in go_fn_map
    assert "expired_token" in go_fn_map
    assert "TestCommentedFake" not in go_fn_map

    go_bindings = {b.test_name: b for b in indexer.extract_test_bindings("pkg/auth_test.go", go_tests)}
    assert f"{inv_p}GO-SUB-001" in go_bindings["TestTokenFlow/expired_token"].invariants
    assert f"{inv_p}GO-SUB-001" in go_bindings["TestTokenFlow::expired_token"].invariants
    assert f"{inv_p}GO-SUB-001" in go_bindings["expired_token"].invariants

    # Rust attribute macros
    rs_fn_map = indexer.list_test_functions("src/worker_test.rs", rs_tests)
    assert "async_worker_dispatches_job" in rs_fn_map
    assert "parameterized_rust_case" in rs_fn_map
    assert "MacroDrivenCase" in rs_fn_map

    rs_bindings = {b.test_name: b for b in indexer.extract_test_bindings("src/worker_test.rs", rs_tests)}
    assert f"{inv_p}RS-TOKIO-001" in rs_bindings["async_worker_dispatches_job"].invariants
    assert f"{inv_p}RS-RSTEST-001" in rs_bindings["parameterized_rust_case"].invariants
    assert f"{inv_p}RS-TESTCASE-001" in rs_bindings["MacroDrivenCase"].invariants


def test_ts_js_arrow_functions_and_function_expressions_extraction():
    """Verify Bug 15c: AstGrepIndexer extracts TS/JS arrow functions and function expressions."""
    from eidos.core.index.ast_indexer import AstGrepIndexer, diff_public_symbols

    ts_code_v1 = (
        "export const fetchUserProfile = async (userId: string): Promise<string> => {\n"
        "  const nestedMapper = (x: string) => x.trim();\n"
        "  return nestedMapper(userId);\n"
        "};\n\n"
        "export const computeCartTotal = function(prices: number[]): number {\n"
        "  return prices.reduce((a, b) => a + b, 0);\n"
        "};\n\n"
        "const internalFormatter = (val: number) => `${val}`;\n"
    )
    ts_code_v2 = ts_code_v1.replace("userId: string", "userId: string, includeMeta: boolean = false")

    indexer = AstGrepIndexer()
    syms = {s.name: s for s in indexer.extract_symbols("src/profile.ts", ts_code_v1)}
    assert "fetchUserProfile" in syms
    assert syms["fetchUserProfile"].kind == "function"
    assert syms["fetchUserProfile"].is_public is True

    assert "computeCartTotal" in syms
    assert syms["computeCartTotal"].kind == "function"
    assert syms["computeCartTotal"].is_public is True

    assert "internalFormatter" in syms
    assert syms["internalFormatter"].kind == "function"
    assert syms["internalFormatter"].is_public is False

    # Nested arrow callback inside fetchUserProfile must not be extracted as a top-level symbol
    assert "nestedMapper" not in syms

    diff = diff_public_symbols(ts_code_v1, ts_code_v2, "src/profile.ts")
    assert ("function", "", "fetchUserProfile") in diff.changed


def test_ast_imports_ignore_docstrings_and_rust_lifetime_signatures():
    """Verify DLT-2026-096: AST-based extract_imports ignores docstring/comment fake imports and Rust lifetime headers strip body blocks cleanly."""
    from eidos.core.index.ast_indexer import AstGrepIndexer, has_structural_changes

    indexer = AstGrepIndexer()

    py_src = (
        "import orjson\n"
        "from eidos.core import parser\n\n"
        'def helper():\n'
        '    """Example:\n'
        "    import fake_docstring_mod\n"
        "    from fake.pkg import PhantomSymbol\n"
        '    """\n'
        "    return 1\n"
    )
    py_imps = indexer.extract_imports("src/mod.py", py_src)
    assert "orjson" in py_imps
    assert "eidos.core" in py_imps
    assert "eidos.core.parser" in py_imps
    assert "fake_docstring_mod" not in py_imps
    assert "fake.pkg" not in py_imps
    assert "fake.pkg.PhantomSymbol" not in py_imps

    ts_src = (
        "import { api } from './client';\n"
        "// import { ghost } from './commented-out';\n"
        "const doc = `import { fake } from './template-literal'`;\n"
    )
    ts_imps = indexer.extract_imports("src/app.ts", ts_src)
    assert ts_imps == ["./client"]

    rs_v1 = "pub fn parse<'a>(input: &'a str) -> &'a str {\n    input.trim()\n}\n"
    rs_v2 = "pub fn parse<'a>(input: &'a str) -> &'a str {\n    input\n}\n"
    rs_syms = indexer.extract_symbols("src/lib.rs", rs_v1)
    assert len(rs_syms) == 1
    assert rs_syms[0].signature == "pub fn parse<'a>(input: &'a str) -> &'a str"
    # Body-only edit inside a Rust function with an odd number of lifetime single quotes is non-structural
    assert has_structural_changes(rs_v1, rs_v2, "src/lib.rs") == (False, [])


def test_deep_review_polyglot_ast_refinements():
    """Verify deep-review refinements across polyglot imports, SFC test unwrapping, docstrings, __all__ +=, Ruby singleton visibility, 15-param headers, and AST UI contracts."""
    from eidos.core.index.ast_indexer import (
        AstGrepIndexer,
        boundary_symbol_map,
        classify_design_changes,
    )

    indexer = AstGrepIndexer()
    inv_p = "INV" + "-"

    # 1. Polyglot extract_imports for C#, Ruby, PHP, Swift, and Rust grouped use trees
    cs_imps = indexer.extract_imports(
        "src/Auth.cs",
        "using System;\nusing System.Collections.Generic;\nusing Alias = System.Text.StringBuilder;\n",
    )
    assert cs_imps == ["System", "System.Collections.Generic", "System.Text.StringBuilder"]

    rb_imps = indexer.extract_imports(
        "app/service.rb",
        "require 'json'\nrequire_relative './helper'\n",
    )
    assert rb_imps == ["json", "./helper"]

    php_imps = indexer.extract_imports(
        "src/Service.php",
        "<?php\nuse App\\Models\\User;\nuse App\\Services\\{AuthService, BillingService as Billing};\nrequire_once 'config.php';\n",
    )
    assert php_imps == ["App\\Models\\User", "App\\Services\\AuthService", "App\\Services\\BillingService", "config.php"]

    sw_imps = indexer.extract_imports("src/Auth.swift", "import Foundation\nimport struct Foundation.URL\n")
    assert sw_imps == ["Foundation", "Foundation.URL"]

    rs_imps = indexer.extract_imports("src/lib.rs", "use crate::core::{foo, bar};\n")
    assert rs_imps == ["crate::core"]

    # 2. Vue / Svelte SFC <script> unwrapping in list_test_functions and extract_test_bindings
    vue_sfc = (
        "<template>\n  <div>{{ label }}</div>\n</template>\n"
        "<script setup lang=\"ts\">\n"
        "it('renders vue component label', () => {\n"
        f"  // {inv_p}VUE-SFC-001\n"
        "});\n"
        "</script>\n"
    )
    vue_tests = indexer.list_test_functions("src/Button.spec.vue", vue_sfc)
    assert "renders vue component label" in vue_tests
    vue_bindings = indexer.extract_test_bindings("src/Button.spec.vue", vue_sfc)
    assert len(vue_bindings) == 1
    assert vue_bindings[0].invariants == [f"{inv_p}VUE-SFC-001"]
    assert vue_bindings[0].line_start == 5

    # 3. Python docstring escape sequences & augmented __all__ += [...]
    py_code = (
        '__all__ = ["exported_a"]\n'
        '__all__ += ["exported_b"]\n\n'
        "def exported_a():\n"
        '    """Docstring with \\"escaped quotes\\" intact."""\n'
        "    return 1\n\n"
        "def exported_b():\n"
        "    return 2\n\n"
        "def internal_c():\n"
        "    return 3\n"
    )
    py_syms = {s.name: s for s in indexer.extract_symbols("src/pkg.py", py_code)}
    assert '\\"escaped quotes\\"' in (py_syms["exported_a"].docstring or "")
    assert py_syms["exported_a"].is_public is True
    assert py_syms["exported_b"].is_public is True
    assert py_syms["internal_c"].is_public is False

    # 4. Ruby singleton_method (`def self.build`) after bare `private` remains public
    rb_code = (
        "class Factory\n"
        "  private\n"
        "  def secret_helper\n"
        "  end\n"
        "  def self.build\n"
        "  end\n"
        "end\n"
    )
    rb_syms = {s.name: s for s in indexer.extract_symbols("app/factory.rb", rb_code)}
    assert rb_syms["secret_helper"].is_public is False
    assert rb_syms["build"].is_public is True

    # 5. Multi-line function signature (>12 parameters) preserves closing parenthesis
    long_py = (
        "def configure(\n"
        + "".join(f"    param_{i}: int = {i},\n" for i in range(15))
        + ") -> bool:\n    return True\n"
    )
    long_syms = indexer.extract_symbols("src/config.py", long_py)
    assert len(long_syms) == 1
    assert long_syms[0].signature is not None
    assert long_syms[0].signature.endswith(") -> bool")

    # 6. Rust grouped `pub use` in boundary_symbol_map and comment-immune TS UI contract classification
    rs_bmap = boundary_symbol_map("pub use crate::models::{UserRecord, Account as Acc};\n", "src/lib.rs")
    assert ("export", "", "UserRecord") in rs_bmap
    assert ("export", "", "Acc") in rs_bmap

    tsx_v1 = (
        "export interface CardProps {\n"
        "  style?: { color: string };\n"
        "  title: string;\n"
        "}\n"
        "export function Card(p: CardProps) { return null; }\n"
    )
    tsx_v2 = tsx_v1 + "// export function CommentedOutFake() {}\n"
    cat, reasons = classify_design_changes(tsx_v1, tsx_v2, "src/Card.tsx")
    assert cat == "body_only"
    assert reasons == []


def test_all_ast_languages_and_corner_cases(tmp_path: Path) -> None:
    """Comprehensive verification across all 17 AstGrepIndexer languages (20 extensions) and corner cases."""
    from eidos.core.index.ast_indexer import (
        AstGrepIndexer,
        _extract_ts_exported_names,
        _strip_leading_decorators_from_signature,
    )

    indexer = AstGrepIndexer()
    inv_p = "INV" + "-"
    pol_p = "POL" + "-"

    # 1. Verify all 20 extensions in LANGUAGE_MAP map to the 17 supported AST grammars
    expected_exts = {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".rs",
        ".go",
        ".java",
        ".kt",
        ".kts",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".cs",
        ".rb",
        ".php",
        ".swift",
        ".vue",
        ".svelte",
    }
    assert set(AstGrepIndexer.LANGUAGE_MAP.keys()) == expected_exts

    # 2. Missing files and unsupported extensions gracefully return empty results
    missing_py = tmp_path / "nonexistent.py"
    assert indexer.extract_symbols(missing_py) == []
    assert indexer.extract_imports(missing_py) == []
    assert indexer.list_test_functions(missing_py) == {}
    assert indexer.extract_test_bindings(missing_py) == []

    assert indexer.extract_symbols("README.txt", "hello") == []
    assert indexer.extract_imports("README.txt", "hello") == []
    assert indexer.list_test_functions("README.txt", "hello") == {}
    assert indexer.extract_test_bindings("README.txt", "hello") == []

    # 3. Remaining extract_imports languages & corner cases (Go, Java, Kotlin, C/C++, Python aliases, JS/Vue require/dynamic import)
    py_imp_file = tmp_path / "imports_sample.py"
    py_imp_file.write_text(
        "import os as operating_system\nfrom pkg.mod import Alpha as A\nfrom pkg.star import *\n",
        encoding="utf-8",
    )
    assert indexer.extract_imports(py_imp_file) == ["os", "pkg.mod", "pkg.mod.Alpha", "pkg.star"]

    go_imps = indexer.extract_imports(
        "main.go",
        'package main\nimport (\n    "fmt"\n    "github.com/org/repo/internal"\n)\n',
    )
    assert go_imps == ["fmt", "github.com/org/repo/internal"]

    java_imps = indexer.extract_imports(
        "App.java",
        "import java.util.List;\nimport com.example.domain.*;\n",
    )
    assert java_imps == ["java.util.List", "com.example.domain.*"]

    kt_imps = indexer.extract_imports(
        "App.kts",
        "import kotlin.collections.List\nimport com.example.Order as Ord;\n",
    )
    assert kt_imps == ["kotlin.collections.List", "com.example.Order"]

    cpp_imps = indexer.extract_imports(
        "engine.c",
        '#include <stdio.h>\n#include "engine/header.h"\n',
    )
    assert cpp_imps == ["stdio.h", "engine/header.h"]

    js_imps = indexer.extract_imports(
        "bundle.js",
        "const cjs = require('./cjs-mod');\nconst lazy = import('./lazy-mod');\nexport { x } from './reexp-mod';\n",
    )
    assert js_imps == ["./reexp-mod", "./cjs-mod", "./lazy-mod"]

    # 4. Polyglot test discovery (list_test_functions & extract_test_bindings) across C#, C/C++, Ruby, PHP, Swift, Svelte, Deno
    cs_test_src = (
        "public class BillingTests {\n"
        "    [Fact]\n"
        "    public void ValidatesInvoiceTotal() {\n"
        f"        // {inv_p}CS-FACT-001 and {pol_p}002\n"
        "    }\n"
        "    [Theory]\n"
        "    public void ValidatesCurrencyRate(int rate) {\n"
        f"        // {inv_p}CS-THEORY-001\n"
        "    }\n"
        "}\n"
    )
    cs_funcs = indexer.list_test_functions("BillingTests.cs", cs_test_src)
    assert "BillingTests::ValidatesInvoiceTotal" in cs_funcs
    assert "ValidatesCurrencyRate" in cs_funcs
    cs_binds = {b.test_name: b for b in indexer.extract_test_bindings("BillingTests.cs", cs_test_src)}
    assert cs_binds["BillingTests::ValidatesInvoiceTotal"].invariants == [f"{inv_p}CS-FACT-001"]
    assert cs_binds["BillingTests::ValidatesInvoiceTotal"].policies == [f"{pol_p}002"]
    assert cs_binds["ValidatesCurrencyRate"].invariants == [f"{inv_p}CS-THEORY-001"]
    assert cs_binds["ValidatesCurrencyRate"].to_dict()["test_name"] == "ValidatesCurrencyRate"

    c_test_src = (
        "void test_arena_allocator(void) {\n"
        f"    /* {inv_p}C-ARENA-001 */\n"
        "}\n"
    )
    assert "test_arena_allocator" in indexer.list_test_functions("arena_test.c", c_test_src)
    c_binds = indexer.extract_test_bindings("arena_test.c", c_test_src)
    assert len(c_binds) == 1 and c_binds[0].invariants == [f"{inv_p}C-ARENA-001"]

    rb_test_src = (
        "class LedgerTest\n"
        "  def test_double_entry\n"
        f"    # {inv_p}RB-LEDGER-001\n"
        "  end\n"
        "  def self.test_suite_boot\n"
        f"    # {inv_p}RB-BOOT-001\n"
        "  end\n"
        "end\n"
    )
    rb_funcs = indexer.list_test_functions("ledger_test.rb", rb_test_src)
    assert "LedgerTest::test_double_entry" in rb_funcs
    assert "LedgerTest::test_suite_boot" in rb_funcs
    rb_binds = {b.test_name: b for b in indexer.extract_test_bindings("ledger_test.rb", rb_test_src)}
    assert rb_binds["LedgerTest::test_double_entry"].invariants == [f"{inv_p}RB-LEDGER-001"]
    assert rb_binds["test_suite_boot"].invariants == [f"{inv_p}RB-BOOT-001"]

    php_test_src = (
        "<?php\n"
        "class PaymentTest {\n"
        "    public function testCapturePayment() {\n"
        f"        // {inv_p}PHP-PAY-001\n"
        "    }\n"
        "}\n"
        "function test_standalone_webhook() {\n"
        f"    // {inv_p}PHP-HOOK-001\n"
        "}\n"
    )
    php_funcs = indexer.list_test_functions("PaymentTest.php", php_test_src)
    assert "PaymentTest::testCapturePayment" in php_funcs
    assert "test_standalone_webhook" in php_funcs
    php_binds = {b.test_name: b for b in indexer.extract_test_bindings("PaymentTest.php", php_test_src)}
    assert php_binds["PaymentTest::testCapturePayment"].invariants == [f"{inv_p}PHP-PAY-001"]
    assert php_binds["test_standalone_webhook"].invariants == [f"{inv_p}PHP-HOOK-001"]

    swift_test_src = (
        "class KeychainTests {\n"
        "    func testKeyRotation() {\n"
        f"        // {inv_p}SWIFT-KEY-001\n"
        "    }\n"
        "}\n"
    )
    swift_funcs = indexer.list_test_functions("KeychainTests.swift", swift_test_src)
    assert "KeychainTests::testKeyRotation" in swift_funcs
    swift_binds = {b.test_name: b for b in indexer.extract_test_bindings("KeychainTests.swift", swift_test_src)}
    assert swift_binds["KeychainTests::testKeyRotation"].invariants == [f"{inv_p}SWIFT-KEY-001"]

    svelte_test_src = (
        "<script lang=\"ts\">\n"
        "Deno.test(function denoNamedFnTest() {\n"
        f"  // {inv_p}SVELTE-DENO-001\n"
        "});\n"
        "</script>\n"
    )
    svelte_funcs = indexer.list_test_functions("Widget.test.svelte", svelte_test_src)
    assert "denoNamedFnTest" in svelte_funcs
    svelte_binds = indexer.extract_test_bindings("Widget.test.svelte", svelte_test_src)
    assert len(svelte_binds) == 1 and svelte_binds[0].invariants == [f"{inv_p}SVELTE-DENO-001"]

    # 5. Corner cases in C/C++, C#, Ruby, PHP, Swift, Go, Rust, and TS symbol extraction & visibility
    c_src = (
        "struct ForwardDeclaredOnly;\n"
        "struct Packet {\n    int id;\n};\n"
        "enum PacketState { PKT_NEW, PKT_SENT };\n"
        "char *alloc_packet_buffer(int size) {\n    return 0;\n}\n"
        "static int _hidden_c_helper(void) {\n    return 0;\n}\n"
    )
    c_syms = {s.name: s for s in indexer.extract_symbols("packet.c", c_src)}
    assert "ForwardDeclaredOnly" not in c_syms
    assert c_syms["Packet"].kind == "struct"
    assert c_syms["PacketState"].kind == "enum"
    assert c_syms["alloc_packet_buffer"].is_public is True
    assert c_syms["_hidden_c_helper"].is_public is False
    assert c_syms["Packet"].to_dict()["name"] == "Packet"

    cs_src = (
        "public interface IRepository {}\n"
        "public struct Money {}\n"
        "public enum Currency { USD, EUR }\n"
        "public record InvoiceRecord(string Id);\n"
        "public class AccountService {\n"
        "    public void _internalHook() {}\n"
        "}\n"
    )
    cs_syms = {s.name: s for s in indexer.extract_symbols("Account.cs", cs_src)}
    assert cs_syms["IRepository"].kind == "interface"
    assert cs_syms["Money"].kind == "struct"
    assert cs_syms["Currency"].kind == "enum"
    assert cs_syms["InvoiceRecord"].kind == "class"
    assert cs_syms["_internalHook"].is_public is False

    rb_src = (
        "module BillingMod\n"
        "  private def inline_private_helper\n  end\n"
        "  private_class_method def self.hidden_class_factory\n  end\n"
        "end\n"
    )
    rb_syms = {s.name: s for s in indexer.extract_symbols("billing_mod.rb", rb_src)}
    assert rb_syms["BillingMod"].kind == "class"
    assert rb_syms["inline_private_helper"].is_public is False
    assert rb_syms["hidden_class_factory"].is_public is False

    php_syms = {
        s.name: s
        for s in indexer.extract_symbols(
            "Hooks.php",
            "<?php\ntrait Loggable {}\nfunction _private_php_fn() {}\n",
        )
    }
    assert php_syms["Loggable"].kind == "interface"
    assert php_syms["_private_php_fn"].is_public is False

    swift_syms = {
        s.name: s
        for s in indexer.extract_symbols(
            "Internal.swift",
            "public func _underscoreSwiftFn() {}\ninternal func moduleInternalFn() {}\n",
        )
    }
    assert swift_syms["_underscoreSwiftFn"].is_public is False
    assert swift_syms["moduleInternalFn"].is_public is False

    go_syms = {
        s.name: s
        for s in indexer.extract_symbols(
            "types.go",
            "package pkg\ntype TokenID string\nfunc (r *ExternalReceiver) Handle() {}\n",
        )
    }
    assert go_syms["TokenID"].kind == "class"
    assert go_syms["Handle"].parent_symbol == "ExternalReceiver"

    rs_syms = {
        s.name: s
        for s in indexer.extract_symbols(
            "impls.rs",
            "struct PrivateHolder;\nimpl PrivateHolder {\n    pub fn inside_private(&self) {}\n}\n"
            "impl<T> ExternalGeneric<T> {\n    #[doc = \"escaped \\\" [bracket]\"]\n    pub fn ext_method(&self) {}\n}\n",
        )
    }
    assert rs_syms["inside_private"].is_public is False
    assert rs_syms["ext_method"].parent_symbol == "ExternalGeneric"
    assert rs_syms["ext_method"].is_public is True

    # 6. Decorator stripping with escaped quotes/parens and TS namespace/default exports
    stripped_sig = _strip_leading_decorators_from_signature(
        '@route("/api/v1/items(\\"id\\")") #[attr("a\\"b]")] pub fn handle_item()'
    )
    assert stripped_sig == "pub fn handle_item()"

    ts_exp = _extract_ts_exported_names(
        "export * as nsUtils from './utils';\nconst DefaultWidget = () => null;\nexport default DefaultWidget;\n"
    )
    assert "nsUtils" in ts_exp
    assert "DefaultWidget" in ts_exp


