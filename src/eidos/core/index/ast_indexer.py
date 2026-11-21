"""Polyglot AST Symbol Indexer and Structural AI Code Graph Engine (CMP-AST-SYMBOL-INDEXER)."""

from __future__ import annotations

import importlib
import inspect
import re
from pathlib import Path
from typing import Any, ClassVar

import orjson
from ast_grep_py import SgRoot

from eidos.core.index.ast_imports import BaseAstIndexer as BaseAstIndexer
from eidos.core.index.ast_preprocess import (
    _RUST_TEST_ATTR_RE,
    _blank_cpp_export_macros,
    _evaluate_boundary_export,
    _evaluate_symbol_visibility,
    _extract_header_signature,
    _extract_preceding_context,
    _extract_sfc_script_blocks,
    _extract_sg_node_name,
    _extract_ts_exported_names,
    _ts_variable_binding_names,
    _unwrap_js_parens,
)
from eidos.core.index.ast_preprocess import (
    INV_OR_POL_TOKEN_RE as INV_OR_POL_TOKEN_RE,
)
from eidos.core.index.ast_preprocess import (
    INVARIANT_PATTERN as INVARIANT_PATTERN,
)
from eidos.core.index.ast_preprocess import (
    POLICY_PATTERN as POLICY_PATTERN,
)
from eidos.core.index.ast_preprocess import (
    CodeSymbol as CodeSymbol,
)
from eidos.core.index.ast_preprocess import (
    TestFunctionBinding as TestFunctionBinding,
)
from eidos.core.index.ast_preprocess import (
    _extract_python_dunder_all_from_source as _extract_python_dunder_all_from_source,
)
from eidos.core.index.ast_preprocess import (
    _is_component_surface_symbol as _is_component_surface_symbol,
)
from eidos.core.index.ast_preprocess import (
    _is_public_symbol as _is_public_symbol,
)
from eidos.core.index.ast_preprocess import (
    _strip_commented_out_code_blocks as _strip_commented_out_code_blocks,
)
from eidos.core.index.ast_preprocess import (
    _strip_leading_decorators_from_signature as _strip_leading_decorators_from_signature,
)


def _compact_variable_signature(raw_text: str, *, max_len: int = 200) -> str:
    """Normalize whitespace and cap variable declaration signatures to prevent multi-line literal bloat."""
    compact = re.sub(r"\s+", " ", raw_text[: max_len * 4].strip())
    if len(compact) > max_len:
        return f"{compact[: max_len - 3].rstrip()}..."
    return compact


def _extract_python_docstring(node: Any) -> str | None:
    """Extract and cleandoc the Python docstring from a class_definition or function_definition SgNode."""
    body = node.field("body")
    if body is None:
        return None
    first_stmt = next((ch for ch in body.children() if ch.kind() not in ("comment", ":", "newline")), None)
    if first_stmt is None:
        return None
    str_node = (
        first_stmt
        if first_stmt.kind() == "string"
        else (
            next((ch for ch in first_stmt.children() if ch.kind() == "string"), None)
            if first_stmt.kind() == "expression_statement"
            else None
        )
    )
    if str_node is None:
        return None
    raw = "".join(c.text() for c in str_node.children() if c.kind() in ("string_content", "escape_sequence"))
    return inspect.cleandoc(raw)


class AstGrepIndexer(BaseAstIndexer):
    """High-performance polyglot AST indexer backed by ast-grep (Tree-sitter core)."""

    LANGUAGE_MAP: ClassVar[dict[str, str]] = {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".mts": "typescript",
        ".cts": "typescript",
        ".js": "javascript",
        ".jsx": "jsx",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".rs": "rust",
        ".go": "go",
        ".java": "java",
        ".kt": "kotlin",
        ".kts": "kotlin",
        ".c": "c",
        ".cpp": "cpp",
        ".h": "cpp",
        ".hpp": "cpp",
        ".cs": "csharp",
        ".rb": "ruby",
        ".php": "php",
        ".swift": "swift",
        ".vue": "vue",
        ".svelte": "svelte",
    }

    _C_CONTROL_KEYWORDS: ClassVar[frozenset[str]] = frozenset(
        {"if", "for", "while", "switch", "return", "sizeof", "catch", "else", "do"}
    )

    def _get_lang(self, path: Path) -> str | None:
        return self.LANGUAGE_MAP.get(path.suffix.lower())

    @staticmethod
    def _node_name(node: Any, lang: str = "") -> str | None:
        return _extract_sg_node_name(node, lang)

    @classmethod
    def _enclosing_class_name(cls, node: Any, lang: str = "") -> str | None:
        names: list[str] = []
        anc = node.parent()
        while anc is not None:
            if anc.kind() in (
                "class_definition",
                "class_declaration",
                "object_declaration",
                "class_specifier",
                "struct_specifier",
                "struct_declaration",
                "record_declaration",
                "class",
                "module",
            ):
                cname = cls._node_name(anc, lang)
                if cname:
                    names.append(cname)
            anc = anc.parent()
        return "::".join(reversed(names)) if names else None

    @staticmethod
    def _extract_decorator_info(node: Any, lang: str = "") -> tuple[int, list[str]]:
        """Return (effective_1based_start_line, normalized_decorator_strings) for a class or function node."""
        rng = node.range()
        start_line = rng.start.line + 1
        decorators: list[str] = []

        # 1. Check parent wrapper node first (Python decorated_definition, TS/JS export_statement)
        parent = node.parent()
        if parent is not None:
            pkind = parent.kind()
            if pkind == "decorated_definition":
                start_line = min(start_line, parent.range().start.line + 1)
                for ch in parent.children():
                    if ch.kind() == "decorator":
                        d_txt = re.sub(r"\s+", " ", ch.text().strip())
                        if d_txt not in decorators:
                            decorators.append(d_txt)
                return start_line, decorators
            if pkind == "export_statement":
                exp_prev_decos: list[str] = []
                eprev = parent.prev()
                while eprev is not None:
                    ekind = eprev.kind()
                    if ekind in ("decorator", "attribute_item"):
                        start_line = min(start_line, eprev.range().start.line + 1)
                        exp_prev_decos.append(re.sub(r"\s+", " ", eprev.text().strip()))
                        eprev = eprev.prev()
                    elif ekind in ("comment", "line_comment", "block_comment"):
                        eprev = eprev.prev()
                    else:
                        break
                if exp_prev_decos:
                    exp_prev_decos.reverse()
                    for d_txt in exp_prev_decos:
                        if d_txt not in decorators:
                            decorators.append(d_txt)
                for ch in parent.children():
                    if ch.kind() == "decorator":
                        start_line = min(start_line, ch.range().start.line + 1)
                        d_txt = re.sub(r"\s+", " ", ch.text().strip())
                        if d_txt not in decorators:
                            decorators.append(d_txt)

        if lang == "python":
            return start_line, decorators

        if lang == "csharp":
            for ch in node.children():
                if ch.kind() == "attribute_list":
                    start_line = min(start_line, ch.range().start.line + 1)
                    d_txt = re.sub(r"\s+", " ", ch.text().strip())
                    if d_txt and d_txt not in decorators:
                        decorators.append(d_txt)

        # 2. Check preceding sibling decorator / attribute_item nodes (e.g. Rust #[...], TS method @decorator)
        sibling_decos: list[str] = []
        prev = node.prev()
        while prev is not None:
            pkind = prev.kind()
            if pkind in ("decorator", "attribute_item"):
                start_line = min(start_line, prev.range().start.line + 1)
                sibling_decos.append(re.sub(r"\s+", " ", prev.text().strip()))
                prev = prev.prev()
            elif pkind in ("comment", "line_comment", "block_comment"):
                prev = prev.prev()
            else:
                break
        if sibling_decos:
            sibling_decos.reverse()
            decorators.extend(sibling_decos)

        return start_line, decorators

    @staticmethod
    def _extract_ts_test_call_title(c: Any) -> str | None:
        """Extract runnable test title from TS/JS it/test (.each/.concurrent/.only) and Deno.test call_expression.

        Disabled test modifiers (.skip, .todo, .fixme, xit, xtest) and suite-only describe(...) wrappers are excluded.
        """
        fn_ident = c.field("function")
        if fn_ident is None:
            return None
        fn_text = re.sub(r"\s+", " ", fn_ident.text().strip())
        is_deno = fn_text == "Deno.test"
        is_direct_or_member = (
            fn_text in ("it", "test")
            or is_deno
            or bool(
                re.match(
                    r"^(?:it|test)(?:\.(?:only|concurrent|serial|fails))+$",
                    fn_text,
                )
            )
        )
        is_parameterized_each = bool(
            re.match(
                r"^(?:it|test)(?:\.(?:only|concurrent|serial|fails))*\.each\s*(?:\(|`|$)",
                fn_text,
            )
        )
        if not (is_direct_or_member or is_parameterized_each):
            return None

        args = c.field("arguments")
        if args is None:
            return None
        arg_children = [ch for ch in args.children() if ch.text() not in ("(", ")", ",")]
        first_arg = arg_children[0] if arg_children else None
        if first_arg is None:
            return None
        raw_arg = first_arg.text().strip()
        if is_deno and any(re.search(r"\bignore\s*:\s*true\b", ch.text()) for ch in arg_children):
            return None
        if raw_arg.startswith(("'", '"', "`")) and len(raw_arg) >= 2:
            title = raw_arg.strip("'\"`")
            return title if title else None
        if is_deno:
            m_obj = re.search(r"""\bname\s*:\s*(['"`])(.*?)\1""", raw_arg)
            if m_obj and m_obj.group(2):
                return m_obj.group(2)
            m_fn = re.search(r"\bfunction\s+([A-Za-z_$][A-Za-z0-9_$]*)", raw_arg)
            if m_fn and m_fn.group(1):
                return m_fn.group(1)
        return None

    @staticmethod
    def _extract_go_subtests(fn_node: Any, lines: list[str]) -> list[tuple[str, int, int, str]]:
        """Return (subtest_name, start_line_1based, end_line_1based, full_text) for t.Run/b.Run calls inside a Go test function."""
        subtests: list[tuple[str, int, int, str]] = []
        for sub_call in fn_node.find_all(kind="call_expression"):
            sub_fn = sub_call.field("function")
            if sub_fn is None or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*\.Run$", sub_fn.text().strip()):
                continue
            args = sub_call.field("arguments")
            if args is None:
                continue
            first_arg = next(
                (ch for ch in args.children() if ch.text() not in ("(", ")", ",")),
                None,
            )
            if first_arg is None:
                continue
            raw_arg = first_arg.text().strip()
            if not (raw_arg.startswith(('"', "`", "'")) and len(raw_arg) >= 2):
                continue
            sub_name = raw_arg.strip("\"'`")
            if not sub_name:
                continue
            srng = sub_call.range()
            s_start = srng.start.line + 1
            s_end = srng.end.line + 1
            s_prefix = _extract_preceding_context(lines, s_start)
            s_text = f"{s_prefix}\n{sub_call.text()}" if s_prefix else sub_call.text()
            subtests.append((sub_name, s_start, s_end, s_text))
        return subtests

    def _extract_sg_symbols_from_block(
        self,
        rel_path: str,
        content: str,
        lang: str,
        *,
        line_offset: int = 0,
        sfc_lang: str | None = None,
    ) -> list[CodeSymbol]:
        parse_content = _blank_cpp_export_macros(content) if lang in ("c", "cpp") else content
        try:
            root = SgRoot(parse_content, lang)
            node = root.root()
        except (RuntimeError, ValueError):
            return []

        symbols: list[CodeSymbol] = []
        content_lines = content.splitlines()
        parse_lines = parse_content.splitlines() if parse_content is not content else content_lines
        py_dunder_all = _extract_python_dunder_all_from_source(content, py_root=node) if lang == "python" else None
        ts_exports = (
            _extract_ts_exported_names(content, ts_root=node)
            if lang in ("typescript", "tsx", "javascript", "jsx")
            else None
        )

        def _node_header_sig(ast_node: Any, rng_obj: Any) -> str:
            s_ln = rng_obj.start.line
            s_col = rng_obj.start.column
            if parse_lines is not content_lines and s_ln < len(content_lines) and s_ln < len(parse_lines) and s_col > 0:
                p_line = parse_lines[s_ln]
                c_line = content_lines[s_ln]
                scan_col = min(s_col, len(p_line), len(c_line))
                while scan_col > 0 and p_line[scan_col - 1].isspace():
                    scan_col -= 1
                if c_line[scan_col:s_col].strip():
                    while scan_col < s_col and c_line[scan_col].isspace():
                        scan_col += 1
                    s_col = scan_col
            body_node = ast_node.field("body")
            if body_node is not None:
                b_start = body_node.range().start
                b_ln = b_start.line
                b_col = b_start.column
                if s_ln == b_ln and s_ln < len(content_lines):
                    return _extract_header_signature(content_lines[s_ln][s_col:b_col])
                if b_ln > s_ln and s_ln < len(content_lines):
                    first = content_lines[s_ln][s_col:]
                    mid = content_lines[s_ln + 1 : min(b_ln, s_ln + 60)]
                    last = [content_lines[b_ln][:b_col]] if b_ln < len(content_lines) and b_ln <= s_ln + 60 else []
                    return _extract_header_signature("\n".join([first, *mid, *last]))
            e_ln = rng_obj.end.line
            if s_ln == e_ln and s_ln < len(content_lines):
                return _extract_header_signature(content_lines[s_ln][s_col : rng_obj.end.column])
            slice_lines = content_lines[s_ln : min(e_ln + 1, s_ln + 60)]
            if not slice_lines:
                return _extract_header_signature(ast_node.text())
            first = slice_lines[0][s_col:]
            return _extract_header_signature("\n".join([first, *slice_lines[1:]]))

        all_fn_kinds = frozenset(
            {
                "function_definition",
                "function_declaration",
                "function_signature",
                "method_definition",
                "method_signature",
                "function_item",
                "function_signature_item",
                "method_declaration",
                "method_elem",
                "method_spec",
                "method",
                "singleton_method",
                "local_function_statement",
            }
        )

        def _extract_interface_member_summary(iface_node: Any, ck_name: str) -> str:
            member_sigs: list[str] = []
            if lang in ("typescript", "tsx") and ck_name == "interface_declaration":
                body = iface_node.field("body") or next(
                    (c for c in iface_node.children() if c.kind() in ("interface_body", "object_type")),
                    None,
                )
                if body is not None:
                    for ch in body.children():
                        if ch.kind() in (
                            "method_signature",
                            "property_signature",
                            "call_signature",
                            "construct_signature",
                            "index_signature",
                        ):
                            msig = re.sub(r"\s+", " ", ch.text().strip()).rstrip(";,")
                            if msig:
                                member_sigs.append(msig)
            elif lang == "go" and ck_name == "type_spec":
                tnode = iface_node.field("type")
                if tnode is not None and "interface" in tnode.kind():
                    for ch in tnode.children():
                        if ch.kind() not in ("interface", "{", "}", ";", ",", "comment"):
                            msig = re.sub(r"\s+", " ", ch.text().strip()).rstrip(";")
                            if msig:
                                member_sigs.append(msig)
            elif lang == "rust" and ck_name == "trait_item":
                body = iface_node.field("body") or next(
                    (c for c in iface_node.children() if c.kind() == "declaration_list"),
                    None,
                )
                if body is not None:
                    for ch in body.children():
                        ch_k = ch.kind()
                        if ch_k == "function_item":
                            msig = _extract_header_signature(ch.text()).rstrip(";").strip()
                            if msig:
                                member_sigs.append(msig)
                        elif ch_k in ("function_signature_item", "associated_type", "const_item", "type_item"):
                            msig = re.sub(r"\s+", " ", ch.text().strip()).rstrip(";")
                            if msig:
                                member_sigs.append(msig)
            elif lang == "java" and ck_name == "interface_declaration":
                body = iface_node.field("body") or next(
                    (c for c in iface_node.children() if c.kind() == "interface_body"),
                    None,
                )
                if body is not None:
                    for ch in body.children():
                        ch_k = ch.kind()
                        if ch_k == "method_declaration":
                            msig = _extract_header_signature(ch.text()).rstrip(";").strip()
                            if msig:
                                member_sigs.append(msig)
                        elif ch_k in ("constant_declaration", "field_declaration"):
                            msig = re.sub(r"\s+", " ", ch.text().strip()).rstrip(";")
                            if msig:
                                member_sigs.append(msig)
            elif lang == "csharp" and ck_name == "interface_declaration":
                body = iface_node.field("body") or next(
                    (c for c in iface_node.children() if c.kind() == "declaration_list"),
                    None,
                )
                if body is not None:
                    for ch in body.children():
                        ch_k = ch.kind()
                        if ch_k == "method_declaration":
                            msig = _extract_header_signature(ch.text()).rstrip(";").strip()
                            if msig:
                                member_sigs.append(msig)
                        elif ch_k in (
                            "property_declaration",
                            "indexer_declaration",
                            "event_field_declaration",
                            "event_declaration",
                        ):
                            msig = re.sub(r"\s+", " ", ch.text().strip()).rstrip(";")
                            if msig:
                                member_sigs.append(msig)
            return f" {{ {'; '.join(member_sigs)} }}" if member_sigs else ""

        # 1. Classes, Structs & Interfaces
        class_kinds = {
            "python": ["class_definition"],
            "typescript": ["class_declaration", "interface_declaration", "type_alias_declaration"],
            "tsx": ["class_declaration", "interface_declaration", "type_alias_declaration"],
            "javascript": ["class_declaration"],
            "jsx": ["class_declaration"],
            "rust": ["struct_item", "enum_item", "trait_item"],
            "go": ["type_spec"],
            "java": ["class_declaration", "interface_declaration"],
            "kotlin": ["class_declaration", "object_declaration"],
            "c": ["struct_specifier", "enum_specifier"],
            "cpp": ["class_specifier", "struct_specifier", "enum_specifier"],
            "csharp": [
                "class_declaration",
                "interface_declaration",
                "struct_declaration",
                "enum_declaration",
                "record_declaration",
            ],
            "ruby": ["class", "module"],
            "php": ["class_declaration", "interface_declaration", "trait_declaration", "enum_declaration"],
            "swift": ["class_declaration", "protocol_declaration"],
        }.get(lang, [])

        class_nodes: dict[str, Any] = {}
        class_is_public: dict[str, bool] = {}
        interface_or_trait_names: set[str] = set()
        for ck in class_kinds:
            for m in node.find_all(kind=ck):
                if lang in ("c", "cpp") and not any(
                    ch.kind() in ("field_declaration_list", "enumerator_list") for ch in m.children()
                ):
                    continue
                anc = m.parent()
                nested_in_fn = False
                while anc is not None:
                    if anc.kind() in all_fn_kinds or (lang == "python" and anc.kind() == "class_definition"):
                        nested_in_fn = True
                        break
                    anc = anc.parent()
                if nested_in_fn:
                    continue
                cname = self._node_name(m, lang)
                if cname:
                    rng = m.range()
                    dec_start, dec_list = self._extract_decorator_info(m, lang)
                    hdr_sig = _node_header_sig(m, rng)
                    if ck == "type_spec":
                        type_node = m.field("type")
                        type_kind = type_node.kind() if type_node is not None else ""
                        if "interface" in type_kind:
                            kind_label = "interface"
                        elif "struct" in type_kind:
                            kind_label = "struct"
                        else:
                            kind_label = "class"
                        sig = f"type {hdr_sig}"
                    elif lang == "kotlin":
                        is_iface = any(c.kind() == "interface" for c in m.children())
                        kind_label = "interface" if is_iface else "class"
                        sig = hdr_sig
                    elif lang == "rust":
                        if ck == "struct_item":
                            kind_label = "struct"
                        elif ck == "enum_item":
                            kind_label = "enum"
                        elif ck == "trait_item":
                            kind_label = "interface"
                        else:
                            kind_label = "class"
                        sig = hdr_sig
                    elif lang in ("c", "cpp"):
                        kind_label = (
                            "struct" if ck == "struct_specifier" else ("enum" if ck == "enum_specifier" else "class")
                        )
                        sig = hdr_sig
                    elif lang == "csharp":
                        kind_label = (
                            "interface"
                            if ck == "interface_declaration"
                            else (
                                "struct"
                                if ck == "struct_declaration"
                                else ("enum" if ck == "enum_declaration" else "class")
                            )
                        )
                        sig = hdr_sig
                    elif lang == "php":
                        kind_label = (
                            "interface"
                            if ck in ("interface_declaration", "trait_declaration")
                            else ("enum" if ck == "enum_declaration" else "class")
                        )
                        sig = hdr_sig
                    elif lang == "swift":
                        if ck == "protocol_declaration":
                            kind_label = "interface"
                        elif any(c.kind() == "struct" for c in m.children()):
                            kind_label = "struct"
                        elif any(c.kind() == "enum" for c in m.children()):
                            kind_label = "enum"
                        else:
                            kind_label = "class"
                        sig = hdr_sig
                    else:
                        kind_label = "interface" if ("interface" in ck or "type_alias" in ck) else "class"
                        sig = hdr_sig
                    if kind_label == "interface" and ck != "type_alias_declaration":
                        interface_or_trait_names.add(cname)
                        sig = f"{sig}{_extract_interface_member_summary(m, ck)}"
                    if lang in ("typescript", "tsx", "javascript", "jsx"):
                        parent = m.parent()
                        if parent is not None and parent.kind() == "ambient_declaration":
                            if not sig.startswith("declare "):
                                sig = f"declare {sig}"
                            parent = parent.parent()
                        if parent is not None and parent.kind() == "export_statement" and not sig.startswith("export "):
                            sig = f"export {sig}"
                    if dec_list:
                        missing_decos = [d for d in dec_list if not sig.startswith(d) and d not in sig]
                        if missing_decos:
                            sig = f"{' '.join(missing_decos)} {sig}"
                    doc = _extract_python_docstring(m) if lang == "python" else None
                    sym = CodeSymbol(
                        name=cname,
                        kind=kind_label,
                        file_path=rel_path,
                        line_start=dec_start + line_offset,
                        line_end=rng.end.line + 1 + line_offset,
                        docstring=doc,
                        signature=sig,
                    )
                    sym.is_public = _evaluate_symbol_visibility(
                        sym,
                        py_dunder_all=py_dunder_all,
                        ts_exports=ts_exports,
                        class_is_public=class_is_public,
                    )
                    sym.is_boundary_export = _evaluate_boundary_export(sym, py_dunder_all=py_dunder_all)
                    class_is_public[cname] = bool(sym.is_public)
                    symbols.append(sym)
                    class_nodes[cname] = m
                    if lang == "csharp" and ck == "record_declaration":
                        plist = m.field("parameters") or next(
                            (ch for ch in m.children() if ch.kind() == "parameter_list"),
                            None,
                        )
                        if plist is not None:
                            for param in plist.children():
                                if param.kind() != "parameter":
                                    continue
                                pname = self._node_name(param, lang)
                                if not pname:
                                    continue
                                prng = param.range()
                                ptxt = re.sub(r"\s+", " ", param.text().strip())
                                psig = f"{'public' if sym.is_public else 'internal'} {ptxt}"
                                psym = CodeSymbol(
                                    name=pname,
                                    kind="method",
                                    file_path=rel_path,
                                    line_start=prng.start.line + 1 + line_offset,
                                    line_end=prng.end.line + 1 + line_offset,
                                    parent_symbol=cname,
                                    signature=psig,
                                    is_public=bool(sym.is_public),
                                    is_boundary_export=bool(sym.is_public),
                                )
                                symbols.append(psym)

        # 2. Functions & Methods
        fn_kinds = {
            "python": ["function_definition"],
            "typescript": ["function_signature", "method_signature", "function_declaration", "method_definition"],
            "tsx": ["function_signature", "method_signature", "function_declaration", "method_definition"],
            "javascript": ["function_declaration", "method_definition"],
            "jsx": ["function_declaration", "method_definition"],
            "rust": ["function_signature_item", "function_item"],
            "go": ["function_declaration", "method_declaration", "method_elem"],
            "java": ["method_declaration"],
            "kotlin": ["function_declaration"],
            "c": ["function_definition", "declaration"],
            "cpp": ["field_declaration", "declaration", "function_definition"],
            "csharp": [
                "method_declaration",
                "property_declaration",
                "event_declaration",
                "event_field_declaration",
                "indexer_declaration",
                "field_declaration",
            ],
            "ruby": ["method", "singleton_method"],
            "php": ["function_definition", "method_declaration"],
            "swift": ["function_declaration"],
        }.get(lang, [])

        class_ranges = [
            (cname, cnode.range().start.line, cnode.range().end.line) for cname, cnode in class_nodes.items()
        ]
        cpp_member_vis: dict[tuple[str, str], bool] = {}
        ts_overload_sig_targets: set[tuple[str, str]] = set()

        for fk in fn_kinds:
            for m in node.find_all(kind=fk):
                if (
                    lang in ("c", "cpp")
                    and fk in ("declaration", "field_declaration")
                    and not list(m.find_all(kind="function_declarator"))
                ):
                    continue
                if lang == "csharp" and fk == "field_declaration":
                    mod_tokens = {ch.text().strip() for ch in m.children() if ch.kind() == "modifier"}
                    if not (mod_tokens & {"public", "internal"}) or "private" in mod_tokens:
                        continue
                # Skip nested local helper functions defined inside another function or method
                anc = m.parent()
                nested_in_fn = False
                enclosing_py_classes = 0
                while anc is not None:
                    if anc.kind() in all_fn_kinds:
                        nested_in_fn = True
                        break
                    if lang == "python" and anc.kind() == "class_definition":
                        enclosing_py_classes += 1
                    anc = anc.parent()
                if nested_in_fn or enclosing_py_classes > 1:
                    continue

                fname = self._node_name(m, lang)
                if not fname:
                    continue
                rng = m.range()
                dec_start, dec_list = self._extract_decorator_info(m, lang)

                # Determine innermost parent class if any
                parent_cls = None
                best_span: int | None = None
                m_start_ln = rng.start.line
                m_end_ln = rng.end.line
                for cname, c_start_ln, c_end_ln in class_ranges:
                    if c_start_ln <= m_start_ln and c_end_ln >= m_end_ln:
                        span = c_end_ln - c_start_ln
                        if best_span is None or span <= best_span:
                            best_span = span
                            parent_cls = cname
                rust_is_trait_impl = False
                if parent_cls is None and fk == "method_declaration":
                    recv = m.field("receiver")
                    if recv is not None:
                        recv_tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", recv.text())
                        for rtok in reversed(recv_tokens):
                            if rtok in class_nodes:
                                parent_cls = rtok
                                break
                        if parent_cls is None and recv_tokens:
                            parent_cls = recv_tokens[-1]
                elif parent_cls is None and lang == "rust":
                    anc_impl = m.parent()
                    while anc_impl is not None:
                        if anc_impl.kind() == "impl_item":
                            rust_is_trait_impl = any(ch.kind() == "for" for ch in anc_impl.children())
                            impl_type = anc_impl.field("type")
                            if impl_type is not None:
                                type_tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", impl_type.text())
                                for rtok in type_tokens:
                                    if rtok in class_nodes:
                                        parent_cls = rtok
                                        break
                                if parent_cls is None and type_tokens:
                                    parent_cls = type_tokens[0]
                            break
                        anc_impl = anc_impl.parent()

                elif parent_cls is None and lang == "cpp":
                    fdecl = m.field("declarator")
                    while fdecl is not None and fdecl.kind() != "function_declarator":
                        fdecl = fdecl.field("declarator") or next(
                            (c for c in fdecl.children() if c.kind() == "function_declarator"),
                            None,
                        )
                    if fdecl is not None:
                        q_inner = fdecl.field("declarator")
                        while q_inner is not None and q_inner.kind() not in (
                            "identifier",
                            "field_identifier",
                            "qualified_identifier",
                            "destructor_name",
                        ):
                            q_inner = q_inner.field("declarator") or next(
                                (
                                    c
                                    for c in q_inner.children()
                                    if c.kind() in ("identifier", "field_identifier", "qualified_identifier")
                                ),
                                None,
                            )
                        if q_inner is not None and q_inner.kind() == "qualified_identifier" and "::" in q_inner.text():
                            qual_prefix = q_inner.text().rsplit("::", 1)[0]
                            qual_prefix_no_tpl = re.sub(r"<[^<>]*>", "", qual_prefix)
                            qual_tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", qual_prefix_no_tpl)
                            for qtok in reversed(qual_tokens):
                                if qtok in class_nodes:
                                    parent_cls = qtok
                                    break
                            if parent_cls is None and qual_tokens and qual_tokens[-1][0].isupper():
                                parent_cls = qual_tokens[-1]

                if lang in ("typescript", "tsx"):
                    ts_target_key = (parent_cls or "", fname)
                    if fk in ("function_signature", "method_signature"):
                        ts_overload_sig_targets.add(ts_target_key)
                    elif (
                        fk in ("function_declaration", "method_definition") and ts_target_key in ts_overload_sig_targets
                    ):
                        continue

                kind_label = "method" if parent_cls or "method" in fk else "function"
                sig = _node_header_sig(m, rng)
                if lang == "csharp" and fk in (
                    "property_declaration",
                    "event_declaration",
                    "event_field_declaration",
                    "indexer_declaration",
                    "field_declaration",
                ):
                    sig = re.split(r"\s*(?:=>|=|\{)", sig, maxsplit=1)[0].rstrip(";").strip()
                if fk in (
                    "function_signature",
                    "method_signature",
                    "function_signature_item",
                    "method_elem",
                    "method_spec",
                ) or (parent_cls in interface_or_trait_names and sig.endswith(";")):
                    sig = sig.rstrip(";").strip()
                cpp_member_is_private = False
                if lang == "cpp" and parent_cls:
                    member_node = m
                    while member_node.parent() is not None and member_node.parent().kind() in (
                        "template_declaration",
                        "friend_declaration",
                    ):
                        member_node = member_node.parent()
                    flist = member_node.parent()
                    if flist is not None and flist.kind() == "field_declaration_list":
                        access_val: str | None = None
                        prev_sib = member_node.prev()
                        while prev_sib is not None:
                            if prev_sib.kind() == "access_specifier":
                                access_val = prev_sib.text().strip()
                                break
                            prev_sib = prev_sib.prev()
                        if access_val is not None:
                            cpp_member_is_private = access_val in ("private", "protected")
                        else:
                            enc_type = flist.parent()
                            if (
                                enc_type is not None
                                and enc_type.kind() == "class_specifier"
                                and any(ch.kind() == "access_specifier" for ch in flist.children())
                            ):
                                cpp_member_is_private = True
                        cpp_member_vis[(parent_cls, fname)] = cpp_member_is_private
                    else:
                        cpp_member_is_private = cpp_member_vis.get((parent_cls, fname), False)
                if lang == "ruby":
                    rb_vis = "public"
                    p_node = m.parent()
                    if p_node is not None and p_node.kind() == "argument_list":
                        p_node = p_node.parent()
                    if p_node is not None and p_node.kind() == "call":
                        m_field = p_node.field("method")
                        if m_field is not None:
                            m_txt = m_field.text().strip()
                            if m_txt in ("private", "protected", "public"):
                                rb_vis = m_txt
                            elif m_txt == "private_class_method":
                                rb_vis = "private"
                    elif fk == "method":
                        prev_sib = m.prev()
                        while prev_sib is not None:
                            if prev_sib.kind() == "identifier" and prev_sib.text().strip() in (
                                "private",
                                "protected",
                                "public",
                            ):
                                rb_vis = prev_sib.text().strip()
                                break
                            prev_sib = prev_sib.prev()
                    if rb_vis in ("private", "protected") and not sig.startswith(f"{rb_vis} "):
                        sig = f"{rb_vis} {sig}"
                elif lang in ("typescript", "tsx", "javascript", "jsx") and kind_label != "method":
                    parent = m.parent()
                    if parent is not None and parent.kind() == "ambient_declaration":
                        if not sig.startswith("declare "):
                            sig = f"declare {sig}"
                        parent = parent.parent()
                    if parent is not None and parent.kind() == "export_statement" and not sig.startswith("export "):
                        sig = f"export {sig}"
                if dec_list:
                    missing_decos = [d for d in dec_list if not sig.startswith(d) and d not in sig]
                    if missing_decos:
                        sig = f"{' '.join(missing_decos)} {sig}"
                doc = _extract_python_docstring(m) if lang == "python" else None
                sym = CodeSymbol(
                    name=fname,
                    kind=kind_label,
                    file_path=rel_path,
                    line_start=dec_start + line_offset,
                    line_end=rng.end.line + 1 + line_offset,
                    docstring=doc,
                    parent_symbol=parent_cls,
                    signature=sig,
                )
                sym.is_public = _evaluate_symbol_visibility(
                    sym,
                    py_dunder_all=py_dunder_all,
                    ts_exports=ts_exports,
                    class_is_public=class_is_public,
                    cpp_member_is_private=cpp_member_is_private,
                    parent_is_interface_or_trait=bool(parent_cls and parent_cls in interface_or_trait_names),
                    rust_is_trait_impl=rust_is_trait_impl,
                )
                sym.is_boundary_export = _evaluate_boundary_export(sym, py_dunder_all=py_dunder_all)
                symbols.append(sym)

        if lang in ("typescript", "tsx", "javascript", "jsx"):
            seen_fn_names = {s.name for s in symbols if s.kind == "function" and not s.parent_symbol}
            seen_top_names = {s.name for s in symbols if not s.parent_symbol}
            callable_val_kinds = (
                "arrow_function",
                "function_expression",
                "function",
                "generator_function",
            )
            fn_like_kinds = all_fn_kinds | set(callable_val_kinds)
            is_js_ts_module = sfc_lang is None
            for vdecl in node.find_all(kind="variable_declarator"):
                val_node = _unwrap_js_parens(vdecl.field("value")) if is_js_ts_module else vdecl.field("value")
                name_node = vdecl.field("name")
                is_callable = val_node is not None and val_node.kind() in callable_val_kinds
                if is_js_ts_module and (name_node is None or name_node.kind() != "identifier"):
                    is_callable = False
                if not is_callable and not is_js_ts_module:
                    continue
                anc = vdecl.parent()
                nested_in_fn_or_cls = False
                stmt_node = vdecl
                is_exported_decl = False
                while anc is not None:
                    akind = anc.kind()
                    if akind in fn_like_kinds or akind in ("class_declaration", "class_body"):
                        nested_in_fn_or_cls = True
                        break
                    if not is_callable and akind not in (
                        "program",
                        "lexical_declaration",
                        "variable_declaration",
                        "export_statement",
                        "ambient_declaration",
                    ):
                        nested_in_fn_or_cls = True
                        break
                    if akind in ("lexical_declaration", "variable_declaration"):
                        stmt_node = anc
                    elif akind == "export_statement":
                        stmt_node = anc
                        is_exported_decl = True
                    anc = anc.parent()
                if nested_in_fn_or_cls:
                    continue
                names = [self._node_name(vdecl, lang)] if is_callable else _ts_variable_binding_names(vdecl)
                for fname in names:
                    if not fname:
                        continue
                    if is_callable and fname in seen_fn_names:
                        continue
                    if not is_callable and fname in seen_top_names:
                        continue
                    if is_callable:
                        seen_fn_names.add(fname)
                    seen_top_names.add(fname)
                    rng = stmt_node.range() if is_callable else vdecl.range()
                    sig = (
                        _node_header_sig(stmt_node, stmt_node.range())
                        if is_callable
                        else _compact_variable_signature(vdecl.text())
                    )
                    if is_exported_decl and not sig.startswith("export "):
                        sig = f"export {sig}"
                    sym = CodeSymbol(
                        name=fname,
                        kind="function" if is_callable else "variable",
                        file_path=rel_path,
                        line_start=rng.start.line + 1 + line_offset,
                        line_end=rng.end.line + 1 + line_offset,
                        parent_symbol=None,
                        signature=sig,
                    )
                    sym.is_public = _evaluate_symbol_visibility(
                        sym,
                        py_dunder_all=py_dunder_all,
                        ts_exports=ts_exports,
                        class_is_public=class_is_public,
                    )
                    sym.is_boundary_export = _evaluate_boundary_export(sym, py_dunder_all=py_dunder_all)
                    symbols.append(sym)

            # CommonJS direct / aliased module-level exports: exports.NAME = ..., module.exports.NAME = ...,
            # and module.exports = { key: expr } for symbols not already declared at top level.
            if is_js_ts_module:
                for stmt in node.children():
                    if stmt.kind() != "expression_statement":
                        continue
                    assign = next((c for c in stmt.children() if c.kind() == "assignment_expression"), None)
                    if assign is None:
                        continue
                    left = assign.field("left")
                    right = assign.field("right")
                    if left is None or right is None or left.kind() != "member_expression":
                        continue
                    left_text = left.text().strip()
                    cjs_entries: list[tuple[str, bool, Any, str]] = []
                    if left_text == "module.exports":
                        rhs = _unwrap_js_parens(right)
                        if rhs is not None and rhs.kind() == "object":
                            for prop in rhs.children():
                                if prop.kind() == "pair":
                                    key = prop.field("key")
                                    pval = _unwrap_js_parens(prop.field("value"))
                                    if key is not None and key.kind() in ("property_identifier", "identifier"):
                                        kname = key.text().strip()
                                        is_fn = pval is not None and (
                                            pval.kind() in callable_val_kinds
                                            or (pval.kind() == "identifier" and pval.text().strip() in seen_fn_names)
                                        )
                                        psig = _compact_variable_signature(prop.text())
                                        cjs_entries.append((kname, is_fn, prop, psig))
                    else:
                        obj = left.field("object")
                        prop_node = left.field("property")
                        if (
                            obj is not None
                            and prop_node is not None
                            and obj.text().strip() in ("exports", "module.exports")
                            and prop_node.kind() == "property_identifier"
                        ):
                            ename = prop_node.text().strip()
                            rhs = _unwrap_js_parens(right)
                            is_fn = rhs is not None and (
                                rhs.kind() in callable_val_kinds
                                or (rhs.kind() == "identifier" and rhs.text().strip() in seen_fn_names)
                            )
                            esig = (
                                _node_header_sig(stmt, stmt.range())
                                if is_fn
                                else _compact_variable_signature(stmt.text())
                            )
                            cjs_entries.append((ename, is_fn, stmt, esig))

                    for ename, is_fn, target_node, esig in cjs_entries:
                        if not ename or ename in seen_top_names:
                            continue
                        seen_top_names.add(ename)
                        if is_fn:
                            seen_fn_names.add(ename)
                        erng = target_node.range()
                        sym = CodeSymbol(
                            name=ename,
                            kind="function" if is_fn else "variable",
                            file_path=rel_path,
                            line_start=erng.start.line + 1 + line_offset,
                            line_end=erng.end.line + 1 + line_offset,
                            parent_symbol=None,
                            signature=esig,
                        )
                        sym.is_public = _evaluate_symbol_visibility(
                            sym,
                            py_dunder_all=py_dunder_all,
                            ts_exports=ts_exports,
                            class_is_public=class_is_public,
                        )
                        sym.is_boundary_export = _evaluate_boundary_export(sym, py_dunder_all=py_dunder_all)
                        symbols.append(sym)

            if sfc_lang == "vue":
                for call in node.find_all(kind="call_expression"):
                    fn_node = call.field("function")
                    if fn_node is not None and fn_node.text().strip() == "defineProps":
                        crng = call.range()
                        csig = _node_header_sig(call, crng)
                        sym = CodeSymbol(
                            name="defineProps",
                            kind="interface",
                            file_path=rel_path,
                            line_start=crng.start.line + 1 + line_offset,
                            line_end=crng.end.line + 1 + line_offset,
                            signature=csig,
                            is_public=True,
                            is_boundary_export=True,
                        )
                        symbols.append(sym)
            elif sfc_lang == "svelte":
                for exp in node.find_all(kind="export_statement"):
                    if not re.match(r"^export\s+let\b", exp.text().strip()):
                        continue
                    erng = exp.range()
                    esig = _node_header_sig(exp, erng)
                    for vd in exp.find_all(kind="variable_declarator"):
                        pname = self._node_name(vd, lang)
                        if pname:
                            sym = CodeSymbol(
                                name=pname,
                                kind="interface",
                                file_path=rel_path,
                                line_start=erng.start.line + 1 + line_offset,
                                line_end=erng.end.line + 1 + line_offset,
                                signature=esig,
                                is_public=True,
                                is_boundary_export=True,
                            )
                            symbols.append(sym)

        return symbols

    def extract_symbols(self, file_path: Path | str, content: str | None = None) -> list[CodeSymbol]:
        p = Path(file_path)
        lang = self._get_lang(p)
        if not lang:
            return []

        if content is None:
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return []

        rel_path = str(p)
        if lang in ("vue", "svelte"):
            symbols: list[CodeSymbol] = []
            for block_text, line_offset in _extract_sfc_script_blocks(content):
                symbols.extend(
                    self._extract_sg_symbols_from_block(
                        rel_path,
                        block_text,
                        "tsx",
                        line_offset=line_offset,
                        sfc_lang=lang,
                    )
                )
            return symbols

        return self._extract_sg_symbols_from_block(rel_path, content, lang)

    def _iter_test_function_spans(
        self, file_path: Path | str, content: str | None = None
    ) -> list[tuple[str, int, int, str]]:
        """Yield (test_name, line_start, line_end, full_text) for every test function/subtest in file_path."""
        p = Path(file_path)
        lang = self._get_lang(p)
        if not lang:
            return []

        if content is None:
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return []

        spans: list[tuple[str, int, int, str]] = []
        sg_lang = "tsx" if lang in ("vue", "svelte") else lang
        blocks = _extract_sfc_script_blocks(content) if lang in ("vue", "svelte") else [(content, 0)]

        for block_text, line_offset in blocks:
            block_path = p.with_suffix(".ts") if lang in ("vue", "svelte") else p
            work_block = _strip_commented_out_code_blocks(block_text, block_path)
            parse_block = _blank_cpp_export_macros(work_block) if sg_lang in ("c", "cpp") else work_block
            try:
                root = SgRoot(parse_block, sg_lang)
                node = root.root()
            except (RuntimeError, ValueError):
                continue

            lines = work_block.splitlines()
            fn_nodes: list[Any] = []
            if sg_lang == "python":
                fn_nodes = node.find_all(kind="function_definition")
            elif sg_lang in ("typescript", "tsx", "javascript", "jsx"):
                fn_nodes = node.find_all(kind="function_declaration")
                calls = node.find_all(kind="call_expression")
                for c in calls:
                    t_title = self._extract_ts_test_call_title(c)
                    if t_title:
                        rng = c.range()
                        start_line = rng.start.line + 1
                        prefix = _extract_preceding_context(lines, start_line)
                        c_text = f"{prefix}\n{c.text()}" if prefix else c.text()
                        spans.append((t_title, start_line + line_offset, rng.end.line + 1 + line_offset, c_text))
            elif sg_lang in ("go", "kotlin", "swift"):
                fn_nodes = node.find_all(kind="function_declaration")
            elif sg_lang == "rust":
                fn_nodes = node.find_all(kind="function_item")
            elif sg_lang in ("java", "csharp"):
                fn_nodes = node.find_all(kind="method_declaration")
            elif sg_lang in ("c", "cpp"):
                fn_nodes = node.find_all(kind="function_definition")
            elif sg_lang == "ruby":
                fn_nodes = node.find_all(kind="method") + node.find_all(kind="singleton_method")
            elif sg_lang == "php":
                fn_nodes = node.find_all(kind="function_definition") + node.find_all(kind="method_declaration")

            for fn in fn_nodes:
                fname = self._node_name(fn, sg_lang)
                if not fname:
                    continue
                rng = fn.range()
                dec_start, dec_list = self._extract_decorator_info(fn, sg_lang)
                if any(
                    re.match(
                        r"^(?:@(?:pytest\.mark\.skip|unittest\.skip|Disabled|Ignore)(?:\s*\(|$)|#\[\s*ignore\b)",
                        d,
                    )
                    or (
                        sg_lang == "csharp"
                        and re.match(
                            r"^\[\s*(?:[\w.]+\.)?(?:Ignore\b|(?:Fact|Theory)\s*\([^]]*\bSkip\s*=)",
                            d,
                            re.IGNORECASE,
                        )
                    )
                    for d in dec_list
                ):
                    continue
                start_line = dec_start
                parent = fn.parent()
                if parent and parent.kind() in ("decorated_definition",):
                    base_text = parent.text()
                elif dec_list and sg_lang == "rust":
                    base_text = f"{chr(10).join(dec_list)}\n{fn.text()}"
                else:
                    base_text = fn.text()
                prefix = _extract_preceding_context(lines, start_line)
                full_text = f"{prefix}\n{base_text}" if prefix else base_text

                is_test = (
                    fname.startswith("test_")
                    or fname.endswith("_test")
                    or (fname.startswith("Test") and sg_lang != "python")
                    or (fname.startswith("test") and sg_lang != "python")
                    or bool(_RUST_TEST_ATTR_RE.search(full_text))
                    or "@Test" in full_text
                    or (
                        sg_lang == "csharp"
                        and bool(
                            re.search(
                                r"\[\s*(?:[\w.]+\.)?(?:Fact|Theory|Test|TestCase|TestCaseSource|TestMethod|DataTestMethod)(?:Attribute)?\s*(?:\(|\]|,)",
                                base_text,
                            )
                        )
                    )
                )
                if not is_test:
                    continue

                eff_start = start_line + line_offset
                end_line = rng.end.line + 1 + line_offset
                cls_name = self._enclosing_class_name(fn, sg_lang)
                if cls_name:
                    spans.append((f"{cls_name}::{fname}", eff_start, end_line, full_text))
                spans.append((fname, eff_start, end_line, full_text))

                if sg_lang == "go":
                    for sub_name, s_start, s_end, sub_text in self._extract_go_subtests(fn, lines):
                        for skey in (f"{fname}/{sub_name}", f"{fname}::{sub_name}", sub_name):
                            spans.append((skey, s_start + line_offset, s_end + line_offset, sub_text))

        return spans

    def list_test_functions(self, file_path: Path | str, content: str | None = None) -> dict[str, str]:
        result: dict[str, str] = {}
        for tname, _lstart, _lend, full_text in self._iter_test_function_spans(file_path, content):
            result[tname] = f"{result[tname]}\n{full_text}" if tname in result else full_text
        return result

    def extract_test_bindings(self, file_path: Path | str, content: str | None = None) -> list[TestFunctionBinding]:
        rel_path = str(Path(file_path))
        by_name: dict[str, TestFunctionBinding] = {}
        for tname, lstart, lend, full_text in self._iter_test_function_spans(file_path, content):
            invs = sorted(set(INVARIANT_PATTERN.findall(full_text)))
            pols = sorted(set(POLICY_PATTERN.findall(full_text)))
            if not invs and not pols:
                continue
            if tname in by_name:
                existing = by_name[tname]
                existing.invariants = sorted(set(existing.invariants) | set(invs))
                existing.policies = sorted(set(existing.policies) | set(pols))
                existing.line_end = max(existing.line_end, lend)
            else:
                by_name[tname] = TestFunctionBinding(
                    test_file=rel_path,
                    test_name=tname,
                    line_start=lstart,
                    line_end=lend,
                    invariants=invs,
                    policies=pols,
                )
        return list(by_name.values())

    def extract_http_endpoints(
        self,
        file_path: Path | str,
        content: str | None = None,
        *,
        _symbols: list[CodeSymbol] | None = None,
    ) -> list[CodeSymbol]:
        """Extract server HTTP route definitions across Python, TS/JS, Go, Rust, Java/Kotlin, and C#."""
        p = Path(file_path)
        if content is None:
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return []
        return importlib.import_module("eidos.core.index.ast_http")._extract_polyglot_http_endpoints(
            self, p, content, _symbols=_symbols
        )

    def extract_http_requests(
        self,
        file_path: Path | str,
        content: str | None = None,
        *,
        _symbols: list[CodeSymbol] | None = None,
    ) -> list[CodeSymbol]:
        """Extract client HTTP request calls across TS/JS/Vue/Svelte and Python."""
        p = Path(file_path)
        if content is None:
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return []
        return importlib.import_module("eidos.core.index.ast_http")._extract_polyglot_http_requests(
            self, p, content, _symbols=_symbols
        )


def _has_function_level_fixture(fslice: str, inv_pattern: re.Pattern[str] = INVARIANT_PATTERN) -> bool:
    """Return True if eidos:fixture appears in the function header, docstring, or leading standalone comment."""
    if "eidos:fixture" not in fslice:
        return False
    lines = fslice.splitlines()
    if not lines:
        return False

    idx = 0
    seen_fn_decl = False
    decorator_depth = 0
    sig_paren_depth = 0
    while idx < len(lines):
        line = lines[idx]
        if "eidos:fixture" in line:
            return True
        idx += 1
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#["):
            comment_free = stripped.split("//", 1)[0].rstrip()
        else:
            comment_free = stripped.split("#", 1)[0].split("//", 1)[0].rstrip()
        if not comment_free:
            continue
        if not seen_fn_decl:
            if decorator_depth > 0 or stripped.startswith("@") or stripped.startswith("#["):
                decorator_depth += sum(1 for c in comment_free if c in "([{") - sum(
                    1 for c in comment_free if c in ")]}"
                )
                if decorator_depth < 0:
                    decorator_depth = 0
                continue
            seen_fn_decl = True
        sig_paren_depth += comment_free.count("(") - comment_free.count(")")
        if sig_paren_depth < 0:
            sig_paren_depth = 0
        if sig_paren_depth == 0 and (comment_free.endswith(":") or comment_free.endswith("{")):
            break

    while idx < len(lines) and not lines[idx].strip():
        idx += 1

    if idx < len(lines):
        stripped = lines[idx].lstrip()
        for quote in ('"""', "'''", 'r"""', "r'''"):
            if stripped.startswith(quote):
                raw_quote = quote[-3:]
                if "eidos:fixture" in lines[idx]:
                    return True
                rest_after_open = stripped[len(quote) :]
                idx += 1
                if raw_quote not in rest_after_open:
                    while idx < len(lines):
                        if "eidos:fixture" in lines[idx]:
                            return True
                        if raw_quote in lines[idx]:
                            idx += 1
                            break
                        idx += 1
                break

    while idx < len(lines):
        stripped = lines[idx].strip()
        if not stripped:
            idx += 1
            continue
        if stripped.startswith("/*"):
            while idx < len(lines):
                cur_line = lines[idx]
                if "eidos:fixture" in cur_line and not inv_pattern.search(cur_line):
                    return True
                idx += 1
                if "*/" in cur_line:
                    break
            continue
        if stripped.startswith("#") or stripped.startswith("//"):
            if "eidos:fixture" in stripped and not inv_pattern.search(stripped):
                return True
            idx += 1
            continue
        break

    return False


def extract_test_file_cache_payload(
    rel_path: str,
    content: str,
    ast_idx: Any = None,
) -> tuple[dict[str, list[str]], list[str], str, str]:
    """Extract fixture-filtered test function tokens and file-level tokens in a single AST pass."""
    if ast_idx is None:
        ast_idx = get_ast_indexer()
    has_fixture = "eidos:fixture" in content
    cleaned_content = _strip_commented_out_code_blocks(content, rel_path)
    raw_funcs = ast_idx.list_test_functions(rel_path, cleaned_content)
    functions: dict[str, list[str]] = {}
    for fname, fslice in raw_funcs.items():
        if not has_fixture:
            fn_tokens = list(dict.fromkeys(INV_OR_POL_TOKEN_RE.findall(fslice)))
        elif _has_function_level_fixture(fslice, INVARIANT_PATTERN):
            fn_tokens = list(
                dict.fromkeys(
                    tok
                    for line in fslice.splitlines()
                    if "eidos:fixture" not in line
                    for tok in INV_OR_POL_TOKEN_RE.findall(line)
                    if tok.startswith("POL-")
                )
            )
        else:
            fn_tokens = list(
                dict.fromkeys(
                    tok
                    for line in fslice.splitlines()
                    if "eidos:fixture" not in line
                    for tok in INV_OR_POL_TOKEN_RE.findall(line)
                )
            )
        if fn_tokens:
            functions[fname] = fn_tokens

    if not has_fixture:
        file_tokens_list = list(dict.fromkeys(INV_OR_POL_TOKEN_RE.findall(cleaned_content)))
    else:
        file_tokens_list = list(
            dict.fromkeys(
                tok
                for line in cleaned_content.splitlines()
                if "eidos:fixture" not in line
                for tok in INV_OR_POL_TOKEN_RE.findall(line)
            )
        )
    return (
        functions,
        file_tokens_list,
        orjson.dumps(functions, option=orjson.OPT_SORT_KEYS).decode("utf-8"),
        orjson.dumps(file_tokens_list).decode("utf-8"),
    )


def get_ast_indexer() -> BaseAstIndexer:
    """Factory retrieving the unified polyglot AST indexer (AstGrepIndexer)."""
    return AstGrepIndexer()


_LAZY_AST_DIFF_EXPORTS = frozenset(
    {
        "AST_DIFF_EXTS",
        "_AST_DIFF_EXTS",
        "STYLE_EXTS",
        "PublicSymbolDiff",
        "public_symbol_map",
        "boundary_symbol_map",
        "diff_symbol_maps",
        "diff_public_symbols",
        "has_structural_changes",
        "classify_design_changes",
        "_extract_ui_contract",
        "_build_overload_safe_symbol_map",
        "_extract_overload_param_suffix",
    }
)
_LAZY_AST_HTTP_EXPORTS = frozenset(
    {
        "normalize_http_route_path",
        "_extract_polyglot_http_endpoints",
        "_extract_polyglot_http_requests",
    }
)


def __getattr__(name: str) -> Any:
    if name in _LAZY_AST_DIFF_EXPORTS:
        return getattr(importlib.import_module("eidos.core.index.ast_diff"), name)
    if name in _LAZY_AST_HTTP_EXPORTS:
        return getattr(importlib.import_module("eidos.core.index.ast_http"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
