"""Polyglot AST Symbol Indexer and Structural AI Code Graph Engine.

Key features:
- High-performance polyglot AST parsing via ast-grep (ast-grep-py) with Python stdlib ast fallback.
- Granular reverse code-to-spec symbol mapping (classes, functions, methods, interfaces).
- Syntax-aware test function extraction for capability invariants (INV-*) and policies (POL-*).
- Token-budgeted structural repo map generation for AI coding assistants.
"""

from __future__ import annotations

import ast
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

# Detect ast-grep-py availability
try:
    from ast_grep_py import SgRoot

    AST_GREP_AVAILABLE = True
except ImportError:
    AST_GREP_AVAILABLE = False


INVARIANT_PATTERN = re.compile(r"\b(INV-[A-Z0-9_-]+)\b")
POLICY_PATTERN = re.compile(r"\b(POL-[A-Z0-9_-]+)\b")


def _extract_header_signature(node_text: str) -> str:
    """Extract normalized declaration header signature (before body block)."""
    first_lines: list[str] = []
    paren_depth = 0
    for line in node_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "//", "/*", "*")):
            continue
        first_lines.append(stripped)
        paren_depth += stripped.count("(") - stripped.count(")")
        if paren_depth <= 0 and (stripped.endswith(":") or stripped.endswith("{") or "{" in stripped):
            break
        if len(first_lines) >= 6:
            break
    header = " ".join(first_lines)
    # Strip trailing body start
    if "{" in header:
        header = header.split("{", 1)[0].strip()
    elif header.endswith(":"):
        header = header[:-1].strip()
    return re.sub(r"\s+", " ", header)


def _extract_preceding_context(lines: list[str], start_line_1based: int) -> str:
    """Collect decorators, attributes, and comments immediately preceding a function definition."""
    collected: list[str] = []
    idx = start_line_1based - 2  # line before start_line_1based (0-indexed)
    while idx >= 0:
        stripped = lines[idx].strip()
        if (
            stripped.startswith(("#", "//", "/*", "*", "*/", "@", "#["))
            or (stripped == ""
            and collected
            and collected[-1].startswith(("@", "#[")))
        ):
            collected.append(stripped)
            idx -= 1
        else:
            break
    collected.reverse()
    return "\n".join(collected)


@dataclass
class CodeSymbol:
    """A granular syntax-level code symbol (class, function, method, interface)."""

    name: str
    kind: str  # "class", "function", "method", "interface", "struct", "enum"
    file_path: str
    line_start: int
    line_end: int
    docstring: str | None = None
    parent_symbol: str | None = None
    signature: str | None = None
    is_public: bool | None = None
    is_boundary_export: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "file_path": self.file_path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "docstring": self.docstring,
            "parent_symbol": self.parent_symbol,
            "signature": self.signature,
            "is_boundary_export": self.is_boundary_export,
        }


@dataclass
class TestFunctionBinding:
    """An automated test function and its bound living specification invariants and policies."""

    test_file: str
    test_name: str
    line_start: int
    line_end: int
    invariants: list[str] = field(default_factory=list)
    policies: list[str] = field(default_factory=list)

    @property
    def identifier(self) -> str:
        return f"{self.test_file}::{self.test_name}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "identifier": self.identifier,
            "test_file": self.test_file,
            "test_name": self.test_name,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "invariants": self.invariants,
            "policies": self.policies,
        }


class BaseAstIndexer(ABC):
    """Abstract base class for polyglot AST indexing and symbol extraction."""

    @abstractmethod
    def extract_symbols(self, file_path: Path | str, content: str | None = None) -> list[CodeSymbol]:
        """Extract top-level and modular code symbols from a source file."""
        pass

    @abstractmethod
    def extract_test_bindings(self, file_path: Path | str, content: str | None = None) -> list[TestFunctionBinding]:
        """Extract automated test functions and bound living spec invariants/policies."""
        pass

    @abstractmethod
    def list_test_functions(self, file_path: Path | str, content: str | None = None) -> dict[str, str]:
        """Return mapping of test function/case name -> full source slice (including decorators/comments)."""
        pass

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


class AstGrepIndexer(BaseAstIndexer):
    """High-performance AST indexer backed by ast-grep (Rust / Tree-sitter core)."""

    LANGUAGE_MAP: ClassVar[dict[str, str]] = {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".js": "javascript",
        ".jsx": "jsx",
        ".rs": "rust",
        ".go": "go",
        ".java": "java",
        ".kt": "kotlin",
        ".kts": "kotlin",
        ".c": "c",
        ".cpp": "cpp",
        ".h": "c",
        ".hpp": "cpp",
        ".cs": "csharp",
        ".rb": "ruby",
        ".php": "php",
        ".swift": "swift",
        ".vue": "vue",
        ".svelte": "svelte",
    }

    _FALLBACK_SIGNATURE_LANGS: ClassVar[frozenset[str]] = frozenset(
        {"c", "cpp", "csharp", "ruby", "php", "swift", "vue", "svelte"}
    )

    def _get_lang(self, path: Path) -> str | None:
        return self.LANGUAGE_MAP.get(path.suffix.lower())

    @staticmethod
    def _node_name(node: Any) -> str | None:
        name_field = node.field("name")
        if name_field is not None:
            return name_field.text()
        for child in node.children():
            if child.kind() in ("type_identifier", "simple_identifier", "identifier"):
                return child.text()
        return None

    @classmethod
    def _enclosing_class_name(cls, node: Any) -> str | None:
        anc = node.parent()
        while anc is not None:
            if anc.kind() in ("class_definition", "class_declaration", "object_declaration"):
                cname = cls._node_name(anc)
                if cname:
                    return cname
            anc = anc.parent()
        return None

    def extract_symbols(self, file_path: Path | str, content: str | None = None) -> list[CodeSymbol]:
        p = Path(file_path)
        lang = self._get_lang(p)
        if not lang or not AST_GREP_AVAILABLE or lang in self._FALLBACK_SIGNATURE_LANGS:
            return FallbackIndexer().extract_symbols(file_path, content)

        if content is None:
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return []

        try:
            root = SgRoot(content, lang)
            node = root.root()
        except (RuntimeError, ValueError):
            return FallbackIndexer().extract_symbols(file_path, content)

        symbols: list[CodeSymbol] = []
        rel_path = str(p)
        py_dunder_all = _extract_python_dunder_all_from_source(content) if lang == "python" else None
        ts_exports = _extract_ts_exported_names(content) if lang in ("typescript", "tsx", "javascript", "jsx") else None

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
        }.get(lang, [])

        class_nodes: dict[str, Any] = {}
        class_is_public: dict[str, bool] = {}
        for ck in class_kinds:
            for m in node.find_all(kind=ck):
                cname = self._node_name(m)
                if cname:
                    rng = m.range()
                    if ck == "type_spec":
                        type_node = m.field("type")
                        type_kind = type_node.kind() if type_node is not None else ""
                        if "interface" in type_kind:
                            kind_label = "interface"
                        elif "struct" in type_kind:
                            kind_label = "struct"
                        else:
                            kind_label = "class"
                        sig = f"type {_extract_header_signature(m.text())}"
                    elif lang == "kotlin":
                        is_iface = any(c.kind() == "interface" for c in m.children())
                        kind_label = "interface" if is_iface else "class"
                        sig = _extract_header_signature(m.text())
                    elif lang == "rust":
                        if ck == "struct_item":
                            kind_label = "struct"
                        elif ck == "enum_item":
                            kind_label = "enum"
                        elif ck == "trait_item":
                            kind_label = "interface"
                        else:
                            kind_label = "class"
                        sig = _extract_header_signature(m.text())
                    else:
                        kind_label = "interface" if ("interface" in ck or "type_alias" in ck) else "class"
                        sig = _extract_header_signature(m.text())
                    if lang in ("typescript", "tsx", "javascript", "jsx"):
                        parent = m.parent()
                        if (
                            parent is not None
                            and parent.kind() == "export_statement"
                            and not sig.startswith("export ")
                        ):
                            sig = f"export {sig}"
                    sym = CodeSymbol(
                        name=cname,
                        kind=kind_label,
                        file_path=rel_path,
                        line_start=rng.start.line + 1,
                        line_end=rng.end.line + 1,
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

        # 2. Functions & Methods
        fn_kinds = {
            "python": ["function_definition"],
            "typescript": ["function_declaration", "method_definition"],
            "tsx": ["function_declaration", "method_definition"],
            "javascript": ["function_declaration", "method_definition"],
            "jsx": ["function_declaration", "method_definition"],
            "rust": ["function_item"],
            "go": ["function_declaration", "method_declaration"],
            "java": ["method_declaration"],
            "kotlin": ["function_declaration"],
        }.get(lang, [])

        all_fn_kinds = frozenset(
            {
                "function_definition",
                "function_declaration",
                "method_definition",
                "function_item",
                "method_declaration",
            }
        )

        for fk in fn_kinds:
            for m in node.find_all(kind=fk):
                # Skip nested local helper functions defined inside another function or method
                anc = m.parent()
                nested_in_fn = False
                while anc is not None:
                    if anc.kind() in all_fn_kinds:
                        nested_in_fn = True
                        break
                    anc = anc.parent()
                if nested_in_fn:
                    continue

                fname = self._node_name(m)
                if not fname:
                    continue
                rng = m.range()

                # Determine parent class if any
                parent_cls = None
                for cname, cnode in class_nodes.items():
                    crng = cnode.range()
                    if crng.start.line <= rng.start.line and crng.end.line >= rng.end.line:
                        parent_cls = cname
                        break
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

                kind_label = "method" if parent_cls or "method" in fk else "function"
                sig = _extract_header_signature(m.text())
                if lang in ("typescript", "tsx", "javascript", "jsx") and kind_label != "method":
                    parent = m.parent()
                    if (
                        parent is not None
                        and parent.kind() == "export_statement"
                        and not sig.startswith("export ")
                    ):
                        sig = f"export {sig}"
                sym = CodeSymbol(
                    name=fname,
                    kind=kind_label,
                    file_path=rel_path,
                    line_start=rng.start.line + 1,
                    line_end=rng.end.line + 1,
                    parent_symbol=parent_cls,
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

        return symbols

    def list_test_functions(self, file_path: Path | str, content: str | None = None) -> dict[str, str]:
        p = Path(file_path)
        lang = self._get_lang(p)
        if not lang or not AST_GREP_AVAILABLE:
            return FallbackIndexer().list_test_functions(file_path, content)

        if content is None:
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return {}

        try:
            root = SgRoot(content, lang)
            node = root.root()
        except (RuntimeError, ValueError):
            return FallbackIndexer().list_test_functions(file_path, content)

        lines = content.splitlines()
        result: dict[str, str] = {}

        fn_nodes: list[Any] = []
        if lang == "python":
            fn_nodes = node.find_all(kind="function_definition")
        elif lang in ("typescript", "tsx", "javascript", "jsx"):
            fn_nodes = node.find_all(kind="function_declaration")
            calls = node.find_all(kind="call_expression")
            for c in calls:
                fn_ident = c.field("function")
                if fn_ident and fn_ident.text() in ("it", "test", "describe"):
                    args = c.field("arguments")
                    if args:
                        arg_node = next(
                            (ch for ch in args.children() if ch.text() not in ("(", ")", ",") and ch.text().strip("'\"`")),
                            None,
                        )
                        if arg_node is not None:
                            t_title = arg_node.text().strip("'\"`")
                            rng = c.range()
                            start_line = rng.start.line + 1
                            prefix = _extract_preceding_context(lines, start_line)
                            c_text = f"{prefix}\n{c.text()}" if prefix else c.text()
                            result[t_title] = c_text
        elif lang in ("go", "kotlin"):
            fn_nodes = node.find_all(kind="function_declaration")
        elif lang == "rust":
            fn_nodes = node.find_all(kind="function_item")
        elif lang == "java":
            fn_nodes = node.find_all(kind="method_declaration")

        for fn in fn_nodes:
            fname = self._node_name(fn)
            if not fname:
                continue
            rng = fn.range()
            start_line = rng.start.line + 1
            parent = fn.parent()
            if parent and parent.kind() in ("decorated_definition",):
                prng = parent.range()
                start_line = prng.start.line + 1
                base_text = parent.text()
            else:
                base_text = fn.text()
            prefix = _extract_preceding_context(lines, start_line)
            full_text = f"{prefix}\n{base_text}" if prefix else base_text

            is_test = (
                fname.startswith("test")
                or fname.endswith("_test")
                or fname.startswith("Test")
                or "#[test]" in full_text
                or "@Test" in full_text
            )
            if is_test:
                cls_name = self._enclosing_class_name(fn)
                if cls_name:
                    qual_key = f"{cls_name}::{fname}"
                    result[qual_key] = f"{result[qual_key]}\n{full_text}" if qual_key in result else full_text
                result[fname] = f"{result[fname]}\n{full_text}" if fname in result else full_text

        return result

    def extract_test_bindings(self, file_path: Path | str, content: str | None = None) -> list[TestFunctionBinding]:
        p = Path(file_path)
        lang = self._get_lang(p)
        if not lang or not AST_GREP_AVAILABLE:
            return FallbackIndexer().extract_test_bindings(file_path, content)

        if content is None:
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return []

        try:
            root = SgRoot(content, lang)
            node = root.root()
        except (RuntimeError, ValueError):
            return FallbackIndexer().extract_test_bindings(file_path, content)

        lines = content.splitlines()
        by_name: dict[str, TestFunctionBinding] = {}
        rel_path = str(p)

        def _record_binding(tname: str, lstart: int, lend: int, invs: list[str], pols: list[str]) -> None:
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
                    invariants=list(invs),
                    policies=list(pols),
                )

        # Detect test function nodes
        fn_nodes: list[Any] = []
        if lang == "python":
            fn_nodes = node.find_all(kind="function_definition")
        elif lang in ("typescript", "tsx", "javascript", "jsx"):
            fn_nodes = node.find_all(kind="function_declaration")
            # Also capture it('...') or test('...') call expressions
            calls = node.find_all(kind="call_expression")
            for c in calls:
                fn_ident = c.field("function")
                if fn_ident and fn_ident.text() in ("it", "test", "describe"):
                    args = c.field("arguments")
                    if args:
                        arg_node = next(
                            (ch for ch in args.children() if ch.text() not in ("(", ")", ",") and ch.text().strip("'\"`")),
                            None,
                        )
                        if arg_node is not None:
                            t_title = arg_node.text().strip("'\"`")
                            rng = c.range()
                            start_line = rng.start.line + 1
                            prefix = _extract_preceding_context(lines, start_line)
                            c_text = f"{prefix}\n{c.text()}" if prefix else c.text()
                            invs = sorted(set(INVARIANT_PATTERN.findall(c_text)))
                            pols = sorted(set(POLICY_PATTERN.findall(c_text)))
                            if invs or pols:
                                _record_binding(t_title, start_line, rng.end.line + 1, invs, pols)
        elif lang in ("go", "kotlin"):
            fn_nodes = node.find_all(kind="function_declaration")
        elif lang == "rust":
            fn_nodes = node.find_all(kind="function_item")
        elif lang == "java":
            fn_nodes = node.find_all(kind="method_declaration")

        for fn in fn_nodes:
            fname = self._node_name(fn)
            if not fname:
                continue
            rng = fn.range()
            start_line = rng.start.line + 1
            parent = fn.parent()
            if parent and parent.kind() in ("decorated_definition",):
                prng = parent.range()
                start_line = prng.start.line + 1
                base_text = parent.text()
            else:
                base_text = fn.text()
            prefix = _extract_preceding_context(lines, start_line)
            fn_text = f"{prefix}\n{base_text}" if prefix else base_text

            # Test function naming conventions
            is_test = (
                fname.startswith("test_")
                or fname.endswith("_test")
                or fname.startswith("Test")  # Go convention
                or (fname.startswith("test") and lang in ("java", "kotlin"))
                or "#[test]" in fn_text
                or "@Test" in fn_text
            )
            if not is_test:
                continue

            invs = sorted(set(INVARIANT_PATTERN.findall(fn_text)))
            pols = sorted(set(POLICY_PATTERN.findall(fn_text)))

            if invs or pols:
                end_line = rng.end.line + 1
                cls_name = self._enclosing_class_name(fn)
                if cls_name:
                    _record_binding(f"{cls_name}::{fname}", start_line, end_line, invs, pols)
                _record_binding(fname, start_line, end_line, invs, pols)

        return list(by_name.values())


def _python_node_signature(node: ast.AST) -> str:
    """Build a deterministic signature string for a Python AST ClassDef or FunctionDef."""
    if isinstance(node, ast.ClassDef):
        bases = [ast.unparse(b) for b in node.bases]
        base_str = f"({', '.join(bases)})" if bases else ""
        return f"class {node.name}{base_str}"
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        args_str = ast.unparse(node.args)
        ret_str = f" -> {ast.unparse(node.returns)}" if node.returns else ""
        return f"{prefix} {node.name}({args_str}){ret_str}"
    return ""


class FallbackIndexer(BaseAstIndexer):
    """Zero-dependency fallback indexer using Python standard library ast and lightweight regex."""

    PYTHON_FN_REGEX = re.compile(r"^[ \t]*(?:async\s+)?def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", re.MULTILINE)
    PYTHON_CLS_REGEX = re.compile(r"^[ \t]*class\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:\(|:)", re.MULTILINE)

    TS_FN_REGEX = re.compile(
        r"^[ \t]*(?:export\s+)?(?:async\s+)?function\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", re.MULTILINE
    )
    TS_CLS_REGEX = re.compile(
        r"^[ \t]*(?:export\s+)?(?:class|interface|type)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.MULTILINE
    )

    GO_TYPE_REGEX = re.compile(r"^type\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+(struct|interface)\b", re.MULTILINE)
    GO_FN_REGEX = re.compile(r"^func\s+(?:\([^)]+\)\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", re.MULTILINE)
    RUST_TYPE_REGEX = re.compile(
        r"^[ \t]*(?:pub(?:\([^)]*\))?\s+)?(struct|enum|trait)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.MULTILINE
    )
    RUST_FN_REGEX = re.compile(r"^[ \t]*(?:pub\s+)?(?:async\s+)?fn\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", re.MULTILINE)
    KT_CLS_REGEX = re.compile(
        r"^[ \t]*(?:(?:public|private|protected|internal|open|abstract|final|sealed|data|value|inner)\s+)*(class|interface|object)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
        re.MULTILINE,
    )
    KT_FN_REGEX = re.compile(
        r"^[ \t]*(?:(?:public|private|protected|internal|open|override|suspend|inline|operator|infix)\s+)*fun\s+(?:<[^>]+>\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\s*\(",
        re.MULTILINE,
    )
    C_TYPE_REGEX = re.compile(
        r"^[ \t]*(?:typedef\s+)?(struct|class|enum)(?:\s+class)?\s+([a-zA-Z_][a-zA-Z0-9_]*)\b",
        re.MULTILINE,
    )
    C_FN_REGEX = re.compile(
        r"^[ \t]*(?:(?:static|inline|extern|virtual|constexpr|explicit|friend)\s+)*(?:(?:const|unsigned|signed|long|short|struct|class|enum|typename)\s+)*[a-zA-Z_][a-zA-Z0-9_:<>]*[\s\*&]+([a-zA-Z_][a-zA-Z0-9_]*)\s*\([^;{}]*\)\s*(?:const\s*)?(?:noexcept\s*)?(?:\{|;|$)",
        re.MULTILINE,
    )
    CS_TYPE_REGEX = re.compile(
        r"^[ \t]*(?:(?:public|private|protected|internal|static|abstract|sealed|partial|readonly)\s+)*(class|interface|struct|enum|record)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
        re.MULTILINE,
    )
    CS_FN_REGEX = re.compile(
        r"^[ \t]*(?:(?:public|private|protected|internal|static|virtual|override|abstract|async|extern|unsafe|new)\s+)+[a-zA-Z_][a-zA-Z0-9_<>,\.\[\]\?\s]*\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:<[^>]+>)?\s*\(",
        re.MULTILINE,
    )
    RB_CLS_REGEX = re.compile(r"^[ \t]*(class|module)\s+([A-Z][a-zA-Z0-9_:]*)", re.MULTILINE)
    RB_FN_REGEX = re.compile(r"^[ \t]*def\s+(?:self\.)?([a-zA-Z_][a-zA-Z0-9_!\?=]*)", re.MULTILINE)
    PHP_CLS_REGEX = re.compile(
        r"^[ \t]*(?:(?:abstract|final|readonly)\s+)*(class|interface|trait|enum)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
        re.MULTILINE,
    )
    PHP_FN_REGEX = re.compile(
        r"^[ \t]*(?:(?:public|private|protected|static|abstract|final)\s+)*function\s+&?([a-zA-Z_][a-zA-Z0-9_]*)\s*\(",
        re.MULTILINE,
    )
    SWIFT_CLS_REGEX = re.compile(
        r"^[ \t]*(?:(?:public|private|fileprivate|internal|open|final|indirect)\s+)*(class|struct|enum|protocol|actor)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
        re.MULTILINE,
    )
    SWIFT_FN_REGEX = re.compile(
        r"^[ \t]*(?:(?:public|private|fileprivate|internal|open|static|class|mutating|nonmutating|override)\s+)*func\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:<[^>]+>)?\s*\(",
        re.MULTILINE,
    )
    _C_CONTROL_KEYWORDS: ClassVar[frozenset[str]] = frozenset(
        {"if", "for", "while", "switch", "return", "sizeof", "catch", "else", "do"}
    )

    @staticmethod
    def _strip_block_comments_preserving_lines(text: str) -> str:
        """Replace C-style /* ... */, HTML <!-- ... -->, and Ruby =begin ... =end block comments with spaces while preserving line numbers."""
        return re.sub(
            r"/\*.*?\*/|<!--.*?-->|^=begin\b.*?^=end\b",
            lambda m: "".join("\n" if ch == "\n" else " " for ch in m.group(0)),
            text,
            flags=re.DOTALL | re.MULTILINE,
        )

    def extract_symbols(self, file_path: Path | str, content: str | None = None) -> list[CodeSymbol]:
        p = Path(file_path)
        if content is None:
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return []

        rel_path = str(p)
        symbols: list[CodeSymbol] = []

        # 1. Pure Python via stdlib ast
        if p.suffix == ".py":
            try:
                tree = ast.parse(content, filename=rel_path)
                py_dunder_all = _extract_python_dunder_all(tree)
                for node in ast.iter_child_nodes(tree):
                    if isinstance(node, ast.ClassDef):
                        cls_sym = CodeSymbol(
                            name=node.name,
                            kind="class",
                            file_path=rel_path,
                            line_start=node.lineno,
                            line_end=getattr(node, "end_lineno", node.lineno),
                            docstring=ast.get_docstring(node),
                            signature=_python_node_signature(node),
                        )
                        cls_sym.is_public = _evaluate_symbol_visibility(cls_sym, py_dunder_all=py_dunder_all)
                        cls_sym.is_boundary_export = _evaluate_boundary_export(cls_sym, py_dunder_all=py_dunder_all)
                        symbols.append(cls_sym)
                        for item in node.body:
                            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                m_sym = CodeSymbol(
                                    name=item.name,
                                    kind="method",
                                    file_path=rel_path,
                                    line_start=item.lineno,
                                    line_end=getattr(item, "end_lineno", item.lineno),
                                    parent_symbol=node.name,
                                    docstring=ast.get_docstring(item),
                                    signature=_python_node_signature(item),
                                )
                                m_sym.is_public = _evaluate_symbol_visibility(
                                    m_sym,
                                    py_dunder_all=py_dunder_all,
                                    class_is_public={node.name: bool(cls_sym.is_public)},
                                )
                                m_sym.is_boundary_export = _evaluate_boundary_export(m_sym, py_dunder_all=py_dunder_all)
                                symbols.append(m_sym)
                    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        fn_sym = CodeSymbol(
                            name=node.name,
                            kind="function",
                            file_path=rel_path,
                            line_start=node.lineno,
                            line_end=getattr(node, "end_lineno", node.lineno),
                            docstring=ast.get_docstring(node),
                            signature=_python_node_signature(node),
                        )
                        fn_sym.is_public = _evaluate_symbol_visibility(fn_sym, py_dunder_all=py_dunder_all)
                        fn_sym.is_boundary_export = _evaluate_boundary_export(fn_sym, py_dunder_all=py_dunder_all)
                        symbols.append(fn_sym)
                return symbols
            except (SyntaxError, ValueError):
                # Fallback to regex if Python code has syntax errors
                pass

        # 2. Regex fallback for polyglot files (with block comments stripped to avoid false positives)
        suffix = p.suffix.lower()
        clean_content = content if suffix == ".py" else self._strip_block_comments_preserving_lines(content)
        lines = clean_content.splitlines()
        ts_exports = (
            _extract_ts_exported_names(clean_content)
            if suffix in (".ts", ".tsx", ".js", ".jsx", ".vue", ".svelte")
            else None
        )
        rb_visibility = "public"
        for idx, line in enumerate(lines, start=1):
            stripped_line = line.strip()
            if not stripped_line or stripped_line.startswith(("//", "#", "*")):
                continue
            sig = _extract_header_signature(line)
            if suffix == ".py":
                for m in self.PYTHON_CLS_REGEX.finditer(line):
                    sym = CodeSymbol(
                        name=m.group(1),
                        kind="class",
                        file_path=rel_path,
                        line_start=idx,
                        line_end=idx,
                        signature=sig,
                    )
                    sym.is_public = _evaluate_symbol_visibility(sym)
                    symbols.append(sym)
                for m in self.PYTHON_FN_REGEX.finditer(line):
                    sym = CodeSymbol(
                        name=m.group(1),
                        kind="function",
                        file_path=rel_path,
                        line_start=idx,
                        line_end=idx,
                        signature=sig,
                    )
                    sym.is_public = _evaluate_symbol_visibility(sym)
                    symbols.append(sym)
            elif suffix in (".ts", ".tsx", ".js", ".jsx", ".vue", ".svelte"):
                for m in self.TS_CLS_REGEX.finditer(line):
                    sym = CodeSymbol(
                        name=m.group(1),
                        kind="interface" if "interface" in line else "class",
                        file_path=rel_path,
                        line_start=idx,
                        line_end=idx,
                        signature=sig,
                    )
                    sym.is_public = _evaluate_symbol_visibility(sym, ts_exports=ts_exports)
                    symbols.append(sym)
                for m in self.TS_FN_REGEX.finditer(line):
                    sym = CodeSymbol(
                        name=m.group(1),
                        kind="function",
                        file_path=rel_path,
                        line_start=idx,
                        line_end=idx,
                        signature=sig,
                    )
                    sym.is_public = _evaluate_symbol_visibility(sym, ts_exports=ts_exports)
                    symbols.append(sym)
                if suffix in (".vue", ".svelte"):
                    for m in _SVELTE_PROP_RE.finditer(line):
                        sym = CodeSymbol(
                            name=m.group(1),
                            kind="interface",
                            file_path=rel_path,
                            line_start=idx,
                            line_end=idx,
                            signature=sig,
                        )
                        sym.is_public = True
                        symbols.append(sym)
                    if "defineProps" in line:
                        sym = CodeSymbol(
                            name="defineProps",
                            kind="interface",
                            file_path=rel_path,
                            line_start=idx,
                            line_end=idx,
                            signature=sig,
                        )
                        sym.is_public = True
                        symbols.append(sym)
            elif suffix == ".go":
                for m in self.GO_TYPE_REGEX.finditer(line):
                    sym = CodeSymbol(
                        name=m.group(1),
                        kind="interface" if m.group(2) == "interface" else "struct",
                        file_path=rel_path,
                        line_start=idx,
                        line_end=idx,
                        signature=sig,
                    )
                    sym.is_public = _evaluate_symbol_visibility(sym)
                    symbols.append(sym)
                for m in self.GO_FN_REGEX.finditer(line):
                    sym = CodeSymbol(
                        name=m.group(1),
                        kind="function",
                        file_path=rel_path,
                        line_start=idx,
                        line_end=idx,
                        signature=sig,
                    )
                    sym.is_public = _evaluate_symbol_visibility(sym)
                    symbols.append(sym)
            elif suffix == ".rs":
                for m in self.RUST_TYPE_REGEX.finditer(line):
                    sym = CodeSymbol(
                        name=m.group(2),
                        kind="interface" if m.group(1) == "trait" else m.group(1),
                        file_path=rel_path,
                        line_start=idx,
                        line_end=idx,
                        signature=sig,
                    )
                    sym.is_public = _evaluate_symbol_visibility(sym)
                    symbols.append(sym)
                for m in self.RUST_FN_REGEX.finditer(line):
                    sym = CodeSymbol(
                        name=m.group(1),
                        kind="function",
                        file_path=rel_path,
                        line_start=idx,
                        line_end=idx,
                        signature=sig,
                    )
                    sym.is_public = _evaluate_symbol_visibility(sym)
                    symbols.append(sym)
            elif suffix in (".kt", ".kts", ".java"):
                for m in self.KT_CLS_REGEX.finditer(line):
                    sym = CodeSymbol(
                        name=m.group(2),
                        kind="interface" if m.group(1) == "interface" else "class",
                        file_path=rel_path,
                        line_start=idx,
                        line_end=idx,
                        signature=sig,
                    )
                    sym.is_public = _evaluate_symbol_visibility(sym)
                    symbols.append(sym)
                for m in self.KT_FN_REGEX.finditer(line):
                    sym = CodeSymbol(
                        name=m.group(1),
                        kind="function",
                        file_path=rel_path,
                        line_start=idx,
                        line_end=idx,
                        signature=sig,
                    )
                    sym.is_public = _evaluate_symbol_visibility(sym)
                    symbols.append(sym)
            elif suffix in (".c", ".cpp", ".h", ".hpp"):
                type_matched = False
                for m in self.C_TYPE_REGEX.finditer(line):
                    type_matched = True
                    sym = CodeSymbol(
                        name=m.group(2),
                        kind="struct" if m.group(1) == "struct" else ("enum" if m.group(1) == "enum" else "class"),
                        file_path=rel_path,
                        line_start=idx,
                        line_end=idx,
                        signature=sig,
                    )
                    sym.is_public = _evaluate_symbol_visibility(sym)
                    symbols.append(sym)
                if not type_matched:
                    for m in self.C_FN_REGEX.finditer(line):
                        fname = m.group(1)
                        if fname in self._C_CONTROL_KEYWORDS:
                            continue
                        sym = CodeSymbol(
                            name=fname,
                            kind="function",
                            file_path=rel_path,
                            line_start=idx,
                            line_end=idx,
                            signature=sig,
                        )
                        sym.is_public = _evaluate_symbol_visibility(sym)
                        symbols.append(sym)
            elif suffix == ".cs":
                type_matched = False
                for m in self.CS_TYPE_REGEX.finditer(line):
                    type_matched = True
                    kind_raw = m.group(1)
                    sym = CodeSymbol(
                        name=m.group(2),
                        kind="interface" if kind_raw == "interface" else ("struct" if kind_raw == "struct" else ("enum" if kind_raw == "enum" else "class")),
                        file_path=rel_path,
                        line_start=idx,
                        line_end=idx,
                        signature=sig,
                    )
                    sym.is_public = _evaluate_symbol_visibility(sym)
                    symbols.append(sym)
                if not type_matched:
                    for m in self.CS_FN_REGEX.finditer(line):
                        fname = m.group(1)
                        if fname in self._C_CONTROL_KEYWORDS:
                            continue
                        sym = CodeSymbol(
                            name=fname,
                            kind="function",
                            file_path=rel_path,
                            line_start=idx,
                            line_end=idx,
                            signature=sig,
                        )
                        sym.is_public = _evaluate_symbol_visibility(sym)
                        symbols.append(sym)
            elif suffix == ".rb":
                if stripped_line in ("private", "protected", "public"):
                    rb_visibility = stripped_line
                    continue
                for m in self.RB_CLS_REGEX.finditer(line):
                    rb_visibility = "public"
                    sym = CodeSymbol(
                        name=m.group(2),
                        kind="class",
                        file_path=rel_path,
                        line_start=idx,
                        line_end=idx,
                        signature=sig,
                    )
                    sym.is_public = _evaluate_symbol_visibility(sym)
                    symbols.append(sym)
                for m in self.RB_FN_REGEX.finditer(line):
                    rb_sig = sig if rb_visibility == "public" else f"{rb_visibility} {sig}"
                    sym = CodeSymbol(
                        name=m.group(1),
                        kind="function",
                        file_path=rel_path,
                        line_start=idx,
                        line_end=idx,
                        signature=rb_sig,
                    )
                    sym.is_public = _evaluate_symbol_visibility(sym)
                    symbols.append(sym)
            elif suffix == ".php":
                for m in self.PHP_CLS_REGEX.finditer(line):
                    sym = CodeSymbol(
                        name=m.group(2),
                        kind="interface" if m.group(1) in ("interface", "trait") else ("enum" if m.group(1) == "enum" else "class"),
                        file_path=rel_path,
                        line_start=idx,
                        line_end=idx,
                        signature=sig,
                    )
                    sym.is_public = _evaluate_symbol_visibility(sym)
                    symbols.append(sym)
                for m in self.PHP_FN_REGEX.finditer(line):
                    sym = CodeSymbol(
                        name=m.group(1),
                        kind="function",
                        file_path=rel_path,
                        line_start=idx,
                        line_end=idx,
                        signature=sig,
                    )
                    sym.is_public = _evaluate_symbol_visibility(sym)
                    symbols.append(sym)
            elif suffix == ".swift":
                for m in self.SWIFT_CLS_REGEX.finditer(line):
                    kind_raw = m.group(1)
                    sym = CodeSymbol(
                        name=m.group(2),
                        kind="interface" if kind_raw == "protocol" else ("struct" if kind_raw == "struct" else ("enum" if kind_raw == "enum" else "class")),
                        file_path=rel_path,
                        line_start=idx,
                        line_end=idx,
                        signature=sig,
                    )
                    sym.is_public = _evaluate_symbol_visibility(sym)
                    symbols.append(sym)
                for m in self.SWIFT_FN_REGEX.finditer(line):
                    sym = CodeSymbol(
                        name=m.group(1),
                        kind="function",
                        file_path=rel_path,
                        line_start=idx,
                        line_end=idx,
                        signature=sig,
                    )
                    sym.is_public = _evaluate_symbol_visibility(sym)
                    symbols.append(sym)

        return symbols

    def list_test_functions(self, file_path: Path | str, content: str | None = None) -> dict[str, str]:
        p = Path(file_path)
        if content is None:
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return {}

        rel_path = str(p)
        result: dict[str, str] = {}

        if p.suffix == ".py":
            try:
                tree = ast.parse(content, filename=rel_path)
                lines = content.splitlines()

                def _visit_py_nodes(nodes: list[ast.stmt], cls_prefix: str | None = None) -> None:
                    for node in nodes:
                        if isinstance(node, ast.ClassDef):
                            next_prefix = f"{cls_prefix}::{node.name}" if cls_prefix else node.name
                            _visit_py_nodes(node.body, next_prefix)
                        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            if node.name.startswith("test_") or node.name.endswith("_test"):
                                start = (
                                    min(d.lineno for d in node.decorator_list)
                                    if getattr(node, "decorator_list", None)
                                    else node.lineno
                                )
                                end = getattr(node, "end_lineno", node.lineno)
                                prefix = _extract_preceding_context(lines, start)
                                body = "\n".join(lines[start - 1 : end])
                                full_text = f"{prefix}\n{body}" if prefix else body
                                if cls_prefix:
                                    qual_key = f"{cls_prefix}::{node.name}"
                                    result[qual_key] = (
                                        f"{result[qual_key]}\n{full_text}" if qual_key in result else full_text
                                    )
                                result[node.name] = (
                                    f"{result[node.name]}\n{full_text}" if node.name in result else full_text
                                )
                            _visit_py_nodes(node.body, cls_prefix)

                _visit_py_nodes(tree.body)
                return result
            except (SyntaxError, ValueError):
                pass

        test_fn_pattern = re.compile(
            r"(?:def\s+|function\s+|fn\s+|fun\s+|func\s+|void\s+|it\s*\(\s*['\"`]|test\s*\(\s*['\"`])([a-zA-Z0-9_\s-]+)"
        )
        lines = content.splitlines()
        current_test = None
        current_start = 1
        collected_lines: list[str] = []

        for idx, line in enumerate(lines, start=1):
            m = test_fn_pattern.search(line)
            if m:
                if current_test:
                    prefix = _extract_preceding_context(lines, current_start)
                    chunk = "\n".join(collected_lines)
                    full_chunk = f"{prefix}\n{chunk}" if prefix else chunk
                    result[current_test] = (
                        f"{result[current_test]}\n{full_chunk}" if current_test in result else full_chunk
                    )
                current_test = m.group(1).strip()
                current_start = idx
                collected_lines = [line]
            elif current_test:
                collected_lines.append(line)

        if current_test:
            prefix = _extract_preceding_context(lines, current_start)
            chunk = "\n".join(collected_lines)
            full_chunk = f"{prefix}\n{chunk}" if prefix else chunk
            result[current_test] = f"{result[current_test]}\n{full_chunk}" if current_test in result else full_chunk

        return result

    def extract_test_bindings(self, file_path: Path | str, content: str | None = None) -> list[TestFunctionBinding]:
        p = Path(file_path)
        if content is None:
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return []

        rel_path = str(p)
        by_name: dict[str, TestFunctionBinding] = {}

        def _record_binding(tname: str, lstart: int, lend: int, invs: list[str], pols: list[str]) -> None:
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
                    invariants=list(invs),
                    policies=list(pols),
                )

        # Python via stdlib ast
        if p.suffix == ".py":
            try:
                tree = ast.parse(content, filename=rel_path)
                lines = content.splitlines()

                def _visit_py_bindings(nodes: list[ast.stmt], cls_prefix: str | None = None) -> None:
                    for node in nodes:
                        if isinstance(node, ast.ClassDef):
                            next_prefix = f"{cls_prefix}::{node.name}" if cls_prefix else node.name
                            _visit_py_bindings(node.body, next_prefix)
                        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            if node.name.startswith("test_") or node.name.endswith("_test"):
                                start = (
                                    min(d.lineno for d in node.decorator_list)
                                    if getattr(node, "decorator_list", None)
                                    else node.lineno
                                )
                                end = getattr(node, "end_lineno", node.lineno)
                                prefix = _extract_preceding_context(lines, start)
                                body = "\n".join(lines[start - 1 : end])
                                fn_body_text = f"{prefix}\n{body}" if prefix else body
                                invs = sorted(set(INVARIANT_PATTERN.findall(fn_body_text)))
                                pols = sorted(set(POLICY_PATTERN.findall(fn_body_text)))
                                if invs or pols:
                                    if cls_prefix:
                                        _record_binding(f"{cls_prefix}::{node.name}", start, end, invs, pols)
                                    _record_binding(node.name, start, end, invs, pols)
                            _visit_py_bindings(node.body, cls_prefix)

                _visit_py_bindings(tree.body)
                return list(by_name.values())
            except (SyntaxError, ValueError):
                pass

        # Regex fallback for tests across languages
        test_fn_pattern = re.compile(
            r"(?:def\s+|function\s+|fn\s+|fun\s+|func\s+|void\s+|it\s*\(\s*['\"`]|test\s*\(\s*['\"`])([a-zA-Z0-9_\s-]+)"
        )
        lines = content.splitlines()
        current_test = None
        current_start = 1
        collected_lines: list[str] = []

        for idx, line in enumerate(lines, start=1):
            m = test_fn_pattern.search(line)
            if m:
                if current_test:
                    prefix = _extract_preceding_context(lines, current_start)
                    body = "\n".join(collected_lines)
                    chunk = f"{prefix}\n{body}" if prefix else body
                    invs = sorted(set(INVARIANT_PATTERN.findall(chunk)))
                    pols = sorted(set(POLICY_PATTERN.findall(chunk)))
                    if invs or pols:
                        _record_binding(current_test, current_start, idx - 1, invs, pols)
                current_test = m.group(1).strip()
                current_start = idx
                collected_lines = [line]
            elif current_test:
                collected_lines.append(line)

        if current_test:
            prefix = _extract_preceding_context(lines, current_start)
            body = "\n".join(collected_lines)
            chunk = f"{prefix}\n{body}" if prefix else body
            invs = sorted(set(INVARIANT_PATTERN.findall(chunk)))
            pols = sorted(set(POLICY_PATTERN.findall(chunk)))
            if invs or pols:
                _record_binding(current_test, current_start, len(lines), invs, pols)

        return list(by_name.values())


_RUST_PUB_RE = re.compile(
    r"(?:^|\s)pub(?:\s*\([^)]*\))?\s+(?:async\s+|unsafe\s+|const\s+|extern\s+(?:\"[^\"]*\"\s+)?)?(?:fn|struct|enum|trait|type)\b"
)
_JAVA_KT_PRIVATE_RE = re.compile(r"(?:^|\s)(?:private|internal)\s+")
_TS_PRIVATE_METHOD_RE = re.compile(r"^(?:private\s+|protected\s+|#)")


def _extract_python_dunder_all(tree: ast.Module) -> set[str] | None:
    """Return explicitly declared __all__ symbol names if defined at Python module top level, else None."""
    names: set[str] = set()
    found = False
    for node in tree.body:
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
                value = node.value
                found = True
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name) and node.target.id == "__all__":
            value = node.value
            found = True
        if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            for elt in value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    names.add(elt.value)
    return names if found else None


def _extract_python_dunder_all_from_source(content: str) -> set[str] | None:
    try:
        return _extract_python_dunder_all(ast.parse(content))
    except (SyntaxError, ValueError):
        return None


def _extract_ts_exported_names(content: str) -> set[str]:
    """Return all identifier names exported via export declarations, export lists, or export default."""
    exports: set[str] = set(_EXPORT_DECL_RE.findall(content))
    for group in _EXPORT_LIST_RE.findall(content):
        for item in group.split(","):
            raw = item.strip()
            if not raw:
                continue
            parts = [p.strip() for p in raw.split(" as ") if p.strip()]
            for p in parts:
                if p.startswith("type "):
                    p = p[5:].strip()
                if p:
                    exports.add(p)
    exports.update(_EXPORT_DEFAULT_RE.findall(content))
    return exports


def _evaluate_symbol_visibility(
    sym: CodeSymbol,
    *,
    py_dunder_all: set[str] | None = None,
    ts_exports: set[str] | None = None,
    class_is_public: dict[str, bool] | None = None,
) -> bool:
    """Evaluate language-native public boundary visibility for a CodeSymbol (DLT-2026-055)."""
    suffix = Path(sym.file_path).suffix.lower()
    sig = (sym.signature or "").strip()

    if suffix == ".go":
        if sym.parent_symbol and (not sym.parent_symbol or not sym.parent_symbol[0].isupper()):
            return False
        return bool(sym.name and sym.name[0].isupper())

    if suffix == ".rs":
        if sym.parent_symbol and class_is_public is not None and not class_is_public.get(sym.parent_symbol, True):
            return False
        if sig:
            return bool(_RUST_PUB_RE.search(sig))
        return not sym.name.startswith("_")

    if suffix in (".ts", ".tsx", ".js", ".jsx", ".vue", ".svelte"):
        if sym.kind == "method":
            if sym.parent_symbol and class_is_public is not None and not class_is_public.get(sym.parent_symbol, False):
                return False
            if sym.parent_symbol and sym.parent_symbol.startswith(("_", "#")):
                return False
            return not (sym.name.startswith(("_", "#")) or _TS_PRIVATE_METHOD_RE.match(sig))
        if sig.startswith("export ") or re.match(r"^export\b", sig) or sym.name == "defineProps":
            return True
        return bool(ts_exports is not None and sym.name in ts_exports)

    if suffix in (".java", ".kt", ".kts"):
        if sym.name.startswith("_"):
            return False
        if sym.parent_symbol and class_is_public is not None and not class_is_public.get(sym.parent_symbol, True):
            return False
        sig_prefix = sig.split("(", 1)[0] if sig else ""
        return not (sig_prefix and _JAVA_KT_PRIVATE_RE.search(sig_prefix))

    if suffix in (".c", ".cpp", ".h", ".hpp"):
        if sym.name.startswith("_"):
            return False
        sig_prefix = sig.split("(", 1)[0] if sig else ""
        return not bool(re.search(r"\bstatic\b", sig_prefix))

    if suffix == ".cs":
        if sym.name.startswith("_"):
            return False
        sig_prefix = sig.split("(", 1)[0] if sig else ""
        return bool(re.search(r"\bpublic\b", sig_prefix)) and not bool(
            re.search(r"\b(?:private|internal|protected)\b", sig_prefix)
        )

    if suffix == ".rb":
        if sym.name.startswith("_"):
            return False
        return not sig.startswith(("private ", "protected "))

    if suffix == ".php":
        if sym.name.startswith("_"):
            return False
        sig_prefix = sig.split("(", 1)[0] if sig else ""
        return not bool(re.search(r"\b(?:private|protected)\b", sig_prefix))

    if suffix == ".swift":
        if sym.name.startswith("_"):
            return False
        sig_prefix = sig.split("(", 1)[0] if sig else ""
        return not bool(re.search(r"\b(?:private|fileprivate|internal)\b", sig_prefix))

    # Python (.py) and default fallback
    if py_dunder_all is not None:
        if sym.kind == "method":
            if sym.parent_symbol not in py_dunder_all:
                return False
            return not sym.name.startswith("_") or sym.name in ("__init__", "__call__")
        return sym.name in py_dunder_all

    if sym.parent_symbol and sym.parent_symbol.startswith("_"):
        return False
    if sym.kind == "method":
        return not sym.name.startswith("_") or sym.name in ("__init__", "__call__")
    return not sym.name.startswith("_")


def _is_public_symbol(sym: CodeSymbol) -> bool:
    """Return True if a code symbol represents a public contract/surface across supported languages."""
    if sym.is_public is not None:
        return sym.is_public
    return _evaluate_symbol_visibility(sym)


_BOUNDARY_ENTRYPOINT_NAMES = frozenset(
    {
        "__init__.py",
        "main.py",
        "cli.py",
        "index.ts",
        "index.tsx",
        "index.js",
        "index.jsx",
        "mod.rs",
        "lib.rs",
        "main.rs",
        "main.go",
    }
)


def _evaluate_boundary_export(
    sym: CodeSymbol,
    *,
    py_dunder_all: set[str] | None = None,
) -> bool:
    """Distinguish package/contract boundary exports from module-internal helper functions (DLT-2026-081)."""
    if not _is_public_symbol(sym):
        return False
    p = Path(sym.file_path)
    suffix = p.suffix.lower()
    fname = p.name.lower()
    if fname in _BOUNDARY_ENTRYPOINT_NAMES:
        return True
    if py_dunder_all is not None:
        if sym.kind == "method":
            return bool(sym.parent_symbol and sym.parent_symbol in py_dunder_all)
        return sym.name in py_dunder_all
    if sym.kind in ("class", "interface", "struct", "enum", "type", "method"):
        return True
    return not (suffix == ".py" and sym.kind == "function")


def _is_boundary_export_symbol(
    sym: CodeSymbol,
    *,
    py_dunder_all: set[str] | None = None,
) -> bool:
    """Return True if a public symbol is a package/contract boundary export rather than a module-internal helper."""
    if sym.is_boundary_export is not None:
        return sym.is_boundary_export
    return _evaluate_boundary_export(sym, py_dunder_all=py_dunder_all)


#: Source extensions whose public symbols are compared structurally.
AST_DIFF_EXTS = frozenset(
    {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".go",
        ".rs",
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
)
_AST_DIFF_EXTS = AST_DIFF_EXTS


@dataclass
class PublicSymbolDiff:
    """Structured diff of public symbols between two versions of a source file (DLT-2026-039, DLT-2026-081).

    Each entry is a ``(kind, parent_symbol, name)`` key with ``parent_symbol == ""`` for top-level symbols.
    """

    added: list[tuple[str, str, str]] = field(default_factory=list)
    removed: list[tuple[str, str, str]] = field(default_factory=list)
    changed: list[tuple[str, str, str]] = field(default_factory=list)
    boundary_added: list[tuple[str, str, str]] = field(default_factory=list)

    @staticmethod
    def qualified_name(key: tuple[str, str, str]) -> str:
        _, parent, name = key
        return f"{parent}.{name}" if parent else name

    def reasons(self) -> list[str]:
        """Human-readable reasons in the stable order: added, removed, changed signature."""
        out = [f"added {k[0]} '{self.qualified_name(k)}'" for k in self.added]
        out += [f"removed {k[0]} '{self.qualified_name(k)}'" for k in self.removed]
        out += [f"changed signature of {k[0]} '{self.qualified_name(k)}'" for k in self.changed]
        return out

    def __bool__(self) -> bool:
        return bool(self.added or self.removed or self.changed)


def public_symbol_map(content: str | None, file_path: Path | str) -> dict[tuple[str, str, str], str]:
    """Map ``(kind, parent_symbol, name) -> signature`` of the public symbols in one file version.

    ``None`` content (absent file) and files without AST support yield an empty map.
    """
    p = Path(file_path)
    suffix = p.suffix.lower()
    if content is None or suffix not in _AST_DIFF_EXTS:
        return {}
    # For Python, use FallbackIndexer (stdlib ast) for canonical ast.unparse signatures;
    # for other languages, use get_ast_indexer().
    indexer: BaseAstIndexer = FallbackIndexer() if suffix == ".py" else get_ast_indexer()
    return {
        (s.kind, s.parent_symbol or "", s.name): (s.signature or "")
        for s in indexer.extract_symbols(p, content)
        if _is_public_symbol(s)
    }


def boundary_symbol_map(content: str | None, file_path: Path | str) -> dict[tuple[str, str, str], str]:
    """Map ``(kind, parent_symbol, name) -> signature`` of package/contract boundary symbols in one file version (DLT-2026-081)."""
    p = Path(file_path)
    suffix = p.suffix.lower()
    if content is None or suffix not in _AST_DIFF_EXTS:
        return {}
    py_dunder_all: set[str] | None = None
    reexported: dict[tuple[str, str, str], str] = {}
    if suffix == ".py":
        try:
            tree = ast.parse(content, filename=str(p))
            py_dunder_all = _extract_python_dunder_all(tree)
            if p.name.lower() == "__init__.py":
                for node in ast.iter_child_nodes(tree):
                    if isinstance(node, ast.ImportFrom):
                        for alias in node.names:
                            bound_name = alias.asname or alias.name
                            if (
                                bound_name != "*"
                                and not bound_name.startswith("_")
                                and (py_dunder_all is None or bound_name in py_dunder_all)
                            ):
                                reexported[("export", "", bound_name)] = f"reexport {bound_name}"
        except (SyntaxError, ValueError):
            py_dunder_all = None
    elif p.name.lower() in ("index.ts", "index.tsx", "index.js", "index.jsx"):
        for name in _extract_ts_exported_names(content):
            if not name.startswith("_"):
                reexported[("export", "", name)] = f"reexport {name}"
    elif p.name.lower() in ("mod.rs", "lib.rs"):
        for m in re.finditer(r"^\s*pub\s+use\s+[^;]*::([A-Za-z_][A-Za-z0-9_]*)\s*;", content, re.MULTILINE):
            reexported[("export", "", m.group(1))] = f"pub use {m.group(1)}"

    indexer: BaseAstIndexer = FallbackIndexer() if suffix == ".py" else get_ast_indexer()
    out: dict[tuple[str, str, str], str] = {
        (s.kind, s.parent_symbol or "", s.name): (s.signature or "")
        for s in indexer.extract_symbols(p, content)
        if _is_boundary_export_symbol(s, py_dunder_all=py_dunder_all)
    }
    for k, v in reexported.items():
        if not any(existing_k[2] == k[2] for existing_k in out):
            out[k] = v
    return out


def diff_symbol_maps(
    old_map: dict[tuple[str, str, str], str],
    new_map: dict[tuple[str, str, str], str],
    *,
    old_boundary_map: dict[tuple[str, str, str], str] | None = None,
    new_boundary_map: dict[tuple[str, str, str], str] | None = None,
) -> PublicSymbolDiff:
    """Diff two ``public_symbol_map()`` results, optionally tracking boundary-only symbol additions."""
    added = sorted(set(new_map) - set(old_map))
    if old_boundary_map is not None and new_boundary_map is not None:
        boundary_added = sorted(set(new_boundary_map) - set(old_boundary_map))
    else:
        boundary_added = list(added)
    return PublicSymbolDiff(
        added=added,
        removed=sorted(set(old_map) - set(new_map)),
        changed=sorted(k for k in set(old_map) & set(new_map) if old_map[k] != new_map[k]),
        boundary_added=boundary_added,
    )


def diff_public_symbols(
    old_content: str | None,
    new_content: str | None,
    file_path: Path | str,
) -> PublicSymbolDiff:
    """Compute added / removed / signature-changed public symbols between two file versions.

    ``None`` content is treated as an empty file (so every public symbol of an added file is ``added``).
    Files without AST support (non-code extensions) yield an empty diff.
    """
    return diff_symbol_maps(
        public_symbol_map(old_content, file_path),
        public_symbol_map(new_content, file_path),
        old_boundary_map=boundary_symbol_map(old_content, file_path),
        new_boundary_map=boundary_symbol_map(new_content, file_path),
    )


def has_structural_changes(
    old_content: str | None,
    new_content: str | None,
    file_path: Path | str,
) -> tuple[bool, list[str]]:
    """Determine if a file change alters public structural symbols (DLT-2026-028).

    Returns (is_structural, reasons).
    - File additions and deletions are always structural.
    - For source code files (.py, .ts, .tsx, .js, .jsx, .go, .rs, .java):
      compares public classes, functions, and methods and their signatures.
      Body-only edits inside existing functions or private _helpers return (False, []).
    - For non-AST schema/config files (e.g., .yaml, .json): any content change is structural.
    """
    p = Path(file_path)
    if old_content is None:
        return True, [f"added file '{p}'"]
    if new_content is None:
        return True, [f"deleted file '{p}'"]
    if old_content == new_content:
        return False, []

    if p.suffix.lower() not in _AST_DIFF_EXTS:
        return True, [f"modified '{p.name}'"]

    reasons = diff_public_symbols(old_content, new_content, p).reasons()
    return bool(reasons), reasons


# ---------------------------------------------------------------------------
# Design-structural change classification (DLT-2026-030)
# ---------------------------------------------------------------------------

STYLE_EXTS = {".css", ".scss", ".sass", ".less"}
UI_COMPONENT_EXTS = {".tsx", ".jsx", ".vue", ".svelte", ".ts", ".js", ".html"}
DESIGN_EXTS = STYLE_EXTS | UI_COMPONENT_EXTS

_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_CSS_CUSTOM_PROP_RE = re.compile(r"(--[A-Za-z0-9_-]+)\s*:\s*([^;}{]+)")
_PREPROC_VAR_RE = re.compile(r"^\s*([$@][A-Za-z0-9_-]+)\s*:\s*([^;]+);", re.MULTILINE)
_AT_TOKEN_BLOCK_RE = re.compile(r"@(theme|keyframes|font-face)\b([^{]*)\{", re.IGNORECASE)
_EXPORT_DECL_RE = re.compile(
    r"\bexport\s+(?:default\s+)?(?:declare\s+)?(?:async\s+)?"
    r"(?:function\*?|const|let|var|class|interface|type|enum)\s+([A-Za-z_$][\w$]*)"
)
_EXPORT_LIST_RE = re.compile(r"\bexport\s*\{([^}]*)\}")
_EXPORT_DEFAULT_RE = re.compile(r"\bexport\s+default\s+([A-Za-z_$][\w$]*)\s*;?")
_PROPS_DECL_RE = re.compile(r"\b(interface|type)\s+([A-Za-z_$][\w$]*Props)\b")
_ROUTE_RE = re.compile(r"""\bpath\s*[:=]\s*\{?\s*["'`]([^"'`]+)["'`]""")
_SVELTE_PROP_RE = re.compile(r"\bexport\s+let\s+([A-Za-z_$][\w$]*)")
_VUE_PROPS_RE = re.compile(r"defineProps\s*(?:<([^>]*)>)?\s*\(([^)]*)\)", re.DOTALL)


def _balanced_block(content: str, open_idx: int) -> str:
    """Return the text of a brace-balanced block starting at content[open_idx] == '{'."""
    depth = 0
    for i in range(open_idx, len(content)):
        ch = content[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return content[open_idx : i + 1]
    return content[open_idx:]


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _extract_style_tokens(content: str) -> set[str]:
    """Extract design-token declarations from a stylesheet (custom props, preprocessor vars, @theme/@keyframes/@font-face)."""
    clean = _CSS_COMMENT_RE.sub("", content)
    tokens: set[str] = set()
    for name, value in _CSS_CUSTOM_PROP_RE.findall(clean):
        tokens.add(f"prop:{name}={_normalize_ws(value)}")
    for name, value in _PREPROC_VAR_RE.findall(clean):
        tokens.add(f"var:{name}={_normalize_ws(value)}")
    for m in _AT_TOKEN_BLOCK_RE.finditer(clean):
        brace_idx = clean.find("{", m.end() - 1)
        block = _balanced_block(clean, brace_idx)
        tokens.add(f"at:{m.group(1).lower()}:{_normalize_ws(m.group(2))}:{_normalize_ws(block)}")
    return tokens


def _extract_ui_contract(content: str, suffix: str) -> dict[str, set[str]]:
    """Extract the public UI contract surface: exported names, *Props shapes, routes, and framework props."""
    exports: set[str] = set(_EXPORT_DECL_RE.findall(content))
    for group in _EXPORT_LIST_RE.findall(content):
        for item in group.split(","):
            name = item.strip().split(" as ")[-1].strip()
            if name:
                exports.add(name)
    exports.update(_EXPORT_DEFAULT_RE.findall(content))

    props: set[str] = set()
    for m in _PROPS_DECL_RE.finditer(content):
        brace_idx = content.find("{", m.end())
        eq_idx = content.find("=", m.end())
        if brace_idx != -1 and (eq_idx == -1 or brace_idx <= eq_idx + 3 or m.group(1) == "interface"):
            body = _balanced_block(content, brace_idx)
        else:
            end = content.find(";", m.end())
            body = content[m.end() : end if end != -1 else len(content)]
        props.add(f"{m.group(2)}:{_normalize_ws(body)}")

    routes = set(_ROUTE_RE.findall(content))
    framework_props: set[str] = set()
    if suffix == ".svelte":
        framework_props.update(_SVELTE_PROP_RE.findall(content))
    if suffix == ".vue":
        for type_arg, runtime_arg in _VUE_PROPS_RE.findall(content):
            framework_props.add(_normalize_ws(f"{type_arg}|{runtime_arg}"))

    return {"exports": exports, "props": props, "routes": routes, "framework_props": framework_props}


def classify_design_changes(
    old_content: str | None,
    new_content: str | None,
    file_path: Path | str,
) -> tuple[str, list[str]]:
    """Classify a frontend/styling diff into a design-drift category (DLT-2026-030).

    Returns (category, reasons) where category is one of:
    - ``"none"``: not a frontend/styling file.
    - ``"token"``: design token mutation (CSS custom properties, preprocessor variables,
      ``@theme`` / ``@keyframes`` / ``@font-face`` blocks, or token/theme module edits).
    - ``"ui_contract"``: exported UI component, ``*Props`` shape, route, or framework prop changes.
    - ``"body_only"``: internal layout tweaks or component internals with no token/contract impact.
    """
    p = Path(file_path)
    suffix = p.suffix.lower()
    if suffix not in DESIGN_EXTS:
        return "none", []

    is_style = suffix in STYLE_EXTS
    if old_content is None or new_content is None:
        verb = "added" if old_content is None else "deleted"
        return ("token" if is_style else "ui_contract"), [f"{verb} file '{p.name}'"]
    if old_content == new_content:
        return "body_only", []

    if is_style:
        old_t, new_t = _extract_style_tokens(old_content), _extract_style_tokens(new_content)
        if old_t != new_t:
            changed = sorted(t.split("=", 1)[0] for t in (old_t ^ new_t))
            return "token", [f"design token declarations changed: {', '.join(changed[:5])}"]
        return "body_only", []

    # Token / theme modules in TS/JS (e.g. md3-tokens.ts, theme.ts): any substantive edit is a token change.
    stem = p.stem.lower()
    if suffix in (".ts", ".js") and ("token" in stem or "theme" in stem):
        if _normalize_ws(old_content) != _normalize_ws(new_content):
            return "token", [f"token/theme module '{p.name}' modified"]
        return "body_only", []

    old_c, new_c = _extract_ui_contract(old_content, suffix), _extract_ui_contract(new_content, suffix)
    reasons: list[str] = []
    for key, label in (
        ("exports", "exported UI symbols"),
        ("props", "component Props contracts"),
        ("routes", "route definitions"),
        ("framework_props", "framework component props"),
    ):
        if old_c[key] != new_c[key]:
            diff = sorted(x.split(":", 1)[0] for x in (old_c[key] ^ new_c[key]))
            reasons.append(f"{label} changed: {', '.join(diff[:5])}")
    if reasons:
        return "ui_contract", reasons
    return "body_only", []


def is_ast_grep_available() -> bool:
    """Check if the high-performance ast-grep engine is installed in the current environment."""
    return AST_GREP_AVAILABLE


def get_ast_indexer(force_fallback: bool = False) -> BaseAstIndexer:
    """Factory retrieving the optimal AST indexer (AstGrepIndexer or FallbackIndexer)."""
    if AST_GREP_AVAILABLE and not force_fallback:
        return AstGrepIndexer()
    return FallbackIndexer()

