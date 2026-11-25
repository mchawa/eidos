"""Abstract AST indexer base class with polyglot import, declared namespace, and repo-map extraction (CMP-AST-SYMBOL-INDEXER)."""

from __future__ import annotations

import contextlib
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from ast_grep_py import SgRoot

from eidos.core.index.ast_preprocess import (
    CodeSymbol,
    TestFunctionBinding,
    _extract_sfc_script_blocks,
)


class BaseAstIndexer(ABC):
    """Abstract base class for polyglot AST indexing and symbol extraction."""

    @abstractmethod
    def extract_symbols(self, file_path: Path | str, content: str | None = None) -> list[CodeSymbol]:
        """Extract top-level and modular code symbols from a source file."""

    @abstractmethod
    def extract_test_bindings(self, file_path: Path | str, content: str | None = None) -> list[TestFunctionBinding]:
        """Extract automated test functions and bound living spec invariants/policies."""

    @abstractmethod
    def list_test_functions(self, file_path: Path | str, content: str | None = None) -> dict[str, str]:
        """Return mapping of test function/case name -> full source slice (including decorators/comments)."""

    def extract_imports(self, file_path: Path | str, content: str | None = None) -> list[str]:
        """Extract imported module/package specifiers from a source file via syntax trees across supported languages."""
        p = Path(file_path)
        suffix = p.suffix.lower()
        if content is None:
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return []

        imports: list[str] = []
        seen: set[str] = set()

        def _add(spec: str) -> None:
            clean = spec.strip()
            if clean and clean not in seen:
                seen.add(clean)
                imports.append(clean)

        with contextlib.suppress(RuntimeError, ValueError):
            if suffix == ".py":
                py_root = SgRoot(content, "python").root()
                for imp_node in py_root.find_all(kind="import_statement"):
                    for ch in imp_node.children():
                        if ch.kind() == "dotted_name":
                            _add(ch.text())
                        elif ch.kind() == "aliased_import":
                            n_field = ch.field("name")
                            if n_field is not None:
                                _add(n_field.text())
                for from_node in py_root.find_all(kind="import_from_statement"):
                    mod_field = from_node.field("module_name")
                    mod = mod_field.text().strip() if mod_field is not None else ""
                    if mod:
                        _add(mod)
                    past_import_kw = False
                    for ch in from_node.children():
                        if ch.kind() == "import":
                            past_import_kw = True
                        elif past_import_kw:
                            tok = ""
                            if ch.kind() == "dotted_name":
                                tok = ch.text().strip()
                            elif ch.kind() == "aliased_import":
                                n_field = ch.field("name")
                                tok = n_field.text().strip() if n_field is not None else ""
                            if tok and tok != "*":
                                _add(f"{mod}.{tok}" if mod and not mod.endswith(".") else f"{mod}{tok}")
                return imports

            if suffix in (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte"):
                ts_lang = (
                    "tsx"
                    if suffix in (".tsx", ".jsx", ".vue", ".svelte")
                    else ("typescript" if suffix in (".ts", ".mts", ".cts") else "javascript")
                )
                blocks = _extract_sfc_script_blocks(content) if suffix in (".vue", ".svelte") else [(content, 0)]
                for block_text, _ in blocks:
                    ts_root = SgRoot(block_text, ts_lang).root()
                    for stmt in ts_root.find_all(kind="import_statement") + ts_root.find_all(kind="export_statement"):
                        src = stmt.field("source")
                        if src is not None:
                            _add(src.text().strip("'\"`"))
                    for call in ts_root.find_all(kind="call_expression"):
                        fn = call.field("function")
                        fn_txt = fn.text().strip() if fn is not None else ""
                        if fn_txt in ("require", "import") or any(ch.kind() == "import" for ch in call.children()):
                            args = call.field("arguments")
                            if args is not None:
                                first_arg = next(
                                    (ch for ch in args.children() if ch.text() not in ("(", ")", ",")),
                                    None,
                                )
                                if first_arg is not None:
                                    raw = first_arg.text().strip()
                                    if raw.startswith(("'", '"', "`")) and len(raw) >= 2:
                                        _add(raw.strip("'\"`"))
                return imports

            if suffix == ".go":
                go_root = SgRoot(content, "go").root()
                for spec in go_root.find_all(kind="import_spec"):
                    path_node = spec.field("path")
                    if path_node is not None:
                        _add(path_node.text().strip('"`'))
                return imports

            if suffix == ".rs":
                rs_root = SgRoot(content, "rust").root()
                cfg_test_re = re.compile(r"\bcfg\s*\([^)]*\btest\b")

                def _emit_rust_use(raw_path: str, mod_stack: list[str]) -> None:
                    clean_path = raw_path.lstrip(":").strip().rstrip(":")
                    if not clean_path:
                        return
                    segs = [s for s in clean_path.split("::") if s]
                    if not segs:
                        return
                    if mod_stack and segs[0] == "super":
                        n_super = 0
                        while n_super < len(segs) and segs[n_super] == "super":
                            n_super += 1
                        tail = segs[n_super:]
                        depth = len(mod_stack)
                        if n_super <= depth:
                            if not tail:
                                return
                            prefix_mods = mod_stack[: depth - n_super]
                            _add("::".join(["self", *prefix_mods, *tail]))
                            return
                        rem_super = n_super - depth
                        _add("::".join(["super"] * rem_super + tail))
                        return
                    if mod_stack and segs[0] == "self":
                        _add("::".join(["self", *mod_stack, *segs[1:]]))
                        return
                    _add("::".join(segs))

                def _walk_rust_use(node: Any, mod_stack: list[str], prefix: str = "") -> None:
                    kind = node.kind()
                    if kind == "use_as_clause":
                        p_node = node.field("path")
                        if p_node is not None:
                            _walk_rust_use(p_node, mod_stack, prefix)
                        return
                    if kind == "use_wildcard":
                        base_txt = node.text().strip().rstrip("*").rstrip(":").strip()
                        full = f"{prefix}::{base_txt}" if (prefix and base_txt) else (prefix or base_txt)
                        if full:
                            _emit_rust_use(full, mod_stack)
                        return
                    if kind == "scoped_use_list":
                        p_node = node.field("path")
                        l_node = node.field("list")
                        base_txt = p_node.text().strip().rstrip(":") if p_node is not None else ""
                        new_prefix = f"{prefix}::{base_txt}" if (prefix and base_txt) else (prefix or base_txt)
                        for ch in l_node.children() if l_node is not None else ():
                            if ch.text() not in ("{", "}", ","):
                                _walk_rust_use(ch, mod_stack, new_prefix)
                        return
                    if kind == "use_list":
                        for ch in node.children():
                            if ch.text() not in ("{", "}", ","):
                                _walk_rust_use(ch, mod_stack, prefix)
                        return
                    txt = node.text().strip().rstrip(":")
                    if not txt:
                        return
                    if txt == "self":
                        if prefix:
                            _emit_rust_use(prefix, mod_stack)
                        return
                    m = re.match(r"^([A-Za-z0-9_:#]+)", txt)
                    if m:
                        clean_leaf = m.group(1).rstrip(":")
                        full = f"{prefix}::{clean_leaf}" if (prefix and clean_leaf) else (prefix or clean_leaf)
                        if full:
                            _emit_rust_use(full, mod_stack)

                def _walk_rust_items(container: Any, mod_stack: list[str]) -> None:
                    pending_cfg_test = False
                    for ch in container.children():
                        ck = ch.kind()
                        if ck == "attribute_item":
                            if cfg_test_re.search(ch.text()):
                                pending_cfg_test = True
                            continue
                        if ck in ("line_comment", "block_comment"):
                            continue
                        is_cfg_test = pending_cfg_test
                        pending_cfg_test = False
                        if is_cfg_test:
                            continue
                        if ck == "use_declaration":
                            arg = ch.field("argument")
                            if arg is not None:
                                _walk_rust_use(arg, mod_stack)
                        elif ck == "mod_item":
                            m_name_node = ch.field("name")
                            m_name = m_name_node.text().strip() if m_name_node is not None else ""
                            m_body = ch.field("body")
                            if m_body is None:
                                if m_name:
                                    _add("::".join(["self", *mod_stack, m_name]))
                            elif m_name:
                                _walk_rust_items(m_body, [*mod_stack, m_name])
                        elif ck in ("function_item", "impl_item", "trait_item", "block"):
                            # Also capture function-scoped use_declaration statements inside production blocks.
                            for udecl in ch.find_all(kind="use_declaration"):
                                arg = udecl.field("argument")
                                if arg is not None:
                                    _walk_rust_use(arg, mod_stack)

                _walk_rust_items(rs_root, [])
                return imports

            if suffix == ".java":
                java_root = SgRoot(content, "java").root()
                for idecl in java_root.find_all(kind="import_declaration"):
                    has_wildcard = any(ch.kind() == "asterisk" for ch in idecl.children())
                    target = next(
                        (
                            ch.text().strip()
                            for ch in idecl.children()
                            if ch.kind() in ("scoped_identifier", "identifier")
                        ),
                        "",
                    )
                    if target:
                        _add(f"{target}.*" if has_wildcard else target)
                return imports

            if suffix in (".kt", ".kts"):
                kt_root = SgRoot(content, "kotlin").root()
                for ihdr in kt_root.find_all(kind="import_header"):
                    txt = re.sub(r"^import\s+", "", ihdr.text().strip())
                    txt = txt.split(" as ", 1)[0].strip().rstrip(";")
                    if txt:
                        _add(txt)
                return imports

            if suffix in (".c", ".cpp", ".h", ".hpp"):
                cpp_root = SgRoot(content, "cpp").root()
                for inc in cpp_root.find_all(kind="preproc_include"):
                    path_node = inc.field("path")
                    if path_node is not None:
                        _add(path_node.text().strip('<>"'))
                return imports

            if suffix == ".cs":
                cs_root = SgRoot(content, "csharp").root()
                for udir in cs_root.find_all(kind="using_directive"):
                    targets = [
                        ch.text().strip() for ch in udir.children() if ch.kind() in ("qualified_name", "identifier")
                    ]
                    if targets:
                        _add(targets[-1])
                return imports

            if suffix == ".rb":
                rb_root = SgRoot(content, "ruby").root()
                for call in rb_root.find_all(kind="call"):
                    m_field = call.field("method")
                    if m_field is None or m_field.text().strip() not in ("require", "require_relative"):
                        continue
                    args = call.field("arguments")
                    if args is not None:
                        for str_node in args.find_all(kind="string"):
                            sc = "".join(c.text() for c in str_node.children() if c.kind() == "string_content")
                            if sc:
                                _add(sc)
                return imports

            if suffix == ".php":
                php_root = SgRoot(content, "php").root()
                for udecl in php_root.find_all(kind="namespace_use_declaration"):
                    prefix_ns = next(
                        (ch.text().strip() for ch in udecl.children() if ch.kind() == "namespace_name"), ""
                    )
                    clauses = list(udecl.find_all(kind="namespace_use_clause"))
                    for clause in clauses:
                        raw_clause = clause.text().strip().split(" as ", 1)[0].strip().lstrip("\\")
                        if raw_clause:
                            _add(f"{prefix_ns}\\{raw_clause}" if prefix_ns else raw_clause)
                for req_kind in (
                    "require_expression",
                    "require_once_expression",
                    "include_expression",
                    "include_once_expression",
                ):
                    for req in php_root.find_all(kind=req_kind):
                        for s_kind in ("string", "encapsed_string"):
                            for s_node in req.find_all(kind=s_kind):
                                val = s_node.text().strip("'\"")
                                if val:
                                    _add(val)
                return imports

            if suffix == ".swift":
                sw_root = SgRoot(content, "swift").root()
                for idecl in sw_root.find_all(kind="import_declaration"):
                    target = next(
                        (
                            ch.text().strip()
                            for ch in idecl.children()
                            if ch.kind() in ("identifier", "simple_identifier")
                        ),
                        "",
                    )
                    if target:
                        _add(target)
                return imports

            return imports

        return imports

    def extract_declared_namespaces(self, file_path: Path | str, content: str | None = None) -> list[str]:
        """Extract declared namespace/package names (e.g. C# namespace, Java/Kotlin package) via syntax trees."""
        p = Path(file_path)
        suffix = p.suffix.lower()
        if suffix not in (".cs", ".java", ".kt", ".kts"):
            return []
        if content is None:
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return []

        namespaces: list[str] = []
        seen: set[str] = set()

        def _add_ns(ns: str) -> None:
            clean = ns.strip().rstrip(";")
            if clean and clean not in seen:
                seen.add(clean)
                namespaces.append(clean)

        if suffix == ".cs":
            try:
                cs_root = SgRoot(content, "csharp").root()
                for fns in cs_root.find_all(kind="file_scoped_namespace_declaration"):
                    nf = fns.field("name")
                    if nf is not None:
                        _add_ns(nf.text())
                for nd in cs_root.find_all(kind="namespace_declaration"):
                    parts: list[str] = []
                    cur: Any = nd
                    while cur is not None:
                        if cur.kind() == "namespace_declaration":
                            nf = cur.field("name")
                            if nf is not None and nf.text().strip():
                                parts.append(nf.text().strip())
                        cur = cur.parent()
                    if parts:
                        _add_ns(".".join(reversed(parts)))
            except (RuntimeError, ValueError):
                pass
            return namespaces

        if suffix == ".java":
            try:
                java_root = SgRoot(content, "java").root()
                for pkg in java_root.find_all(kind="package_declaration"):
                    target = next(
                        (
                            ch.text().strip()
                            for ch in pkg.children()
                            if ch.kind() in ("scoped_identifier", "identifier")
                        ),
                        "",
                    )
                    if target:
                        _add_ns(target)
            except (RuntimeError, ValueError):
                pass
            return namespaces

        try:
            kt_root = SgRoot(content, "kotlin").root()
            for pkg in kt_root.find_all(kind="package_header"):
                target = next(
                    (ch.text().strip() for ch in pkg.children() if ch.kind() in ("identifier", "simple_identifier")),
                    "",
                )
                if target:
                    _add_ns(target)
        except (RuntimeError, ValueError):
            pass
        return namespaces

    def generate_repo_map(self, symbols_by_file: dict[str, list[CodeSymbol]], max_tokens: int = 1500) -> str:
        """Generate a token-budgeted structural repo map outline for AI coding agents."""
        max_chars = max_tokens * 4  # Standard ~4 characters per token heuristic
        lines: list[str] = ["# 🗺️ Structural Code Graph (Repo Map)", ""]

        current_chars = sum(len(line) + 1 for line in lines)

        for rel_path, symbols in sorted(symbols_by_file.items()):
            if not symbols:
                continue

            file_header = f"`{rel_path}`:"
            file_lines = [file_header]

            classes = [s for s in symbols if s.kind in ("class", "interface", "struct", "enum")]
            standalone_funcs = [s for s in symbols if s.kind == "function" and not s.parent_symbol]

            for cls in classes:
                file_lines.append(f"  ├── {cls.kind} {cls.name} (L{cls.line_start}-L{cls.line_end})")
                methods = [s for s in symbols if s.kind == "method" and s.parent_symbol == cls.name]
                for m in methods:
                    # Prefer public methods or important lifecycle methods
                    if not m.name.startswith("_") or m.name in ("__init__", "__call__"):
                        file_lines.append(f"  │     ├── def {m.name} (L{m.line_start})")

            for fn in standalone_funcs:
                if not fn.name.startswith("_") or fn.name.startswith("__"):
                    file_lines.append(f"  ├── def {fn.name} (L{fn.line_start})")

            file_lines.append("")
            file_block = "\n".join(file_lines)

            if current_chars + len(file_block) > max_chars and len(lines) > 2:
                lines.append("... [remaining modules truncated to preserve context token budget]")
                break

            lines.append(file_block)
            current_chars += len(file_block) + 1

        return "\n".join(lines).strip()
