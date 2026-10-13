"""Polyglot AST Symbol Indexer and Structural AI Code Graph Engine.

Implements DLT-2026-021 / CAP-AGENT-002:
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
    }

    def _get_lang(self, path: Path) -> str | None:
        return self.LANGUAGE_MAP.get(path.suffix.lower())

    def extract_symbols(self, file_path: Path | str, content: str | None = None) -> list[CodeSymbol]:
        p = Path(file_path)
        lang = self._get_lang(p)
        if not lang or not AST_GREP_AVAILABLE:
            return []

        if content is None:
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                return []

        try:
            root = SgRoot(content, lang)
            node = root.root()
        except Exception:
            return []

        symbols: list[CodeSymbol] = []
        rel_path = str(p)

        # 1. Classes & Interfaces
        class_kinds = {
            "python": ["class_definition"],
            "typescript": ["class_declaration", "interface_declaration"],
            "tsx": ["class_declaration", "interface_declaration"],
            "javascript": ["class_declaration"],
            "jsx": ["class_declaration"],
            "rust": ["struct_item", "enum_item", "trait_item"],
            "go": ["type_declaration"],
            "java": ["class_declaration", "interface_declaration"],
        }.get(lang, [])

        class_nodes: dict[str, Any] = {}
        for ck in class_kinds:
            for m in node.find_all(kind=ck):
                name_field = m.field("name")
                if name_field:
                    cname = name_field.text()
                    rng = m.range()
                    kind_label = "interface" if "interface" in ck else "class"
                    symbols.append(
                        CodeSymbol(
                            name=cname,
                            kind=kind_label,
                            file_path=rel_path,
                            line_start=rng.start.line + 1,
                            line_end=rng.end.line + 1,
                            signature=_extract_header_signature(m.text()),
                        )
                    )
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
        }.get(lang, [])

        for fk in fn_kinds:
            for m in node.find_all(kind=fk):
                name_field = m.field("name")
                if not name_field:
                    continue
                fname = name_field.text()
                rng = m.range()

                # Determine parent class if any
                parent_cls = None
                for cname, cnode in class_nodes.items():
                    crng = cnode.range()
                    if crng.start.line <= rng.start.line and crng.end.line >= rng.end.line:
                        parent_cls = cname
                        break

                kind_label = "method" if parent_cls or "method" in fk else "function"
                symbols.append(
                    CodeSymbol(
                        name=fname,
                        kind=kind_label,
                        file_path=rel_path,
                        line_start=rng.start.line + 1,
                        line_end=rng.end.line + 1,
                        parent_symbol=parent_cls,
                        signature=_extract_header_signature(m.text()),
                    )
                )

        return symbols

    def list_test_functions(self, file_path: Path | str, content: str | None = None) -> dict[str, str]:
        p = Path(file_path)
        lang = self._get_lang(p)
        if not lang or not AST_GREP_AVAILABLE:
            return FallbackIndexer().list_test_functions(file_path, content)

        if content is None:
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                return {}

        try:
            root = SgRoot(content, lang)
            node = root.root()
        except Exception:
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
                        first_arg = args.children()
                        if first_arg:
                            t_title = first_arg[0].text().strip("'\"`")
                            rng = c.range()
                            start_line = rng.start.line + 1
                            prefix = _extract_preceding_context(lines, start_line)
                            c_text = f"{prefix}\n{c.text()}" if prefix else c.text()
                            result[t_title] = c_text
        elif lang == "go":
            fn_nodes = node.find_all(kind="function_declaration")
        elif lang == "rust":
            fn_nodes = node.find_all(kind="function_item")
        elif lang == "java":
            fn_nodes = node.find_all(kind="method_declaration")

        for fn in fn_nodes:
            n_field = fn.field("name")
            if not n_field:
                continue
            fname = n_field.text()
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
                result[fname] = full_text

        return result

    def extract_test_bindings(self, file_path: Path | str, content: str | None = None) -> list[TestFunctionBinding]:
        p = Path(file_path)
        lang = self._get_lang(p)
        if not lang or not AST_GREP_AVAILABLE:
            return []

        if content is None:
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                return []

        try:
            root = SgRoot(content, lang)
            node = root.root()
        except Exception:
            return []

        lines = content.splitlines()
        bindings: list[TestFunctionBinding] = []
        rel_path = str(p)

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
                        first_arg = args.children()
                        if first_arg:
                            t_title = first_arg[0].text().strip("'\"`")
                            rng = c.range()
                            start_line = rng.start.line + 1
                            prefix = _extract_preceding_context(lines, start_line)
                            c_text = f"{prefix}\n{c.text()}" if prefix else c.text()
                            invs = sorted(set(INVARIANT_PATTERN.findall(c_text)))
                            pols = sorted(set(POLICY_PATTERN.findall(c_text)))
                            if invs or pols:
                                bindings.append(
                                    TestFunctionBinding(
                                        test_file=rel_path,
                                        test_name=t_title,
                                        line_start=start_line,
                                        line_end=rng.end.line + 1,
                                        invariants=invs,
                                        policies=pols,
                                    )
                                )
        elif lang == "go":
            fn_nodes = node.find_all(kind="function_declaration")
        elif lang == "rust":
            fn_nodes = node.find_all(kind="function_item")
        elif lang == "java":
            fn_nodes = node.find_all(kind="method_declaration")

        for fn in fn_nodes:
            n_field = fn.field("name")
            if not n_field:
                continue
            fname = n_field.text()
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
                or (fname.startswith("test") and lang == "java")
                or "#[test]" in fn_text
                or "@Test" in fn_text
            )
            if not is_test:
                continue

            invs = sorted(set(INVARIANT_PATTERN.findall(fn_text)))
            pols = sorted(set(POLICY_PATTERN.findall(fn_text)))

            if invs or pols:
                bindings.append(
                    TestFunctionBinding(
                        test_file=rel_path,
                        test_name=fname,
                        line_start=start_line,
                        line_end=rng.end.line + 1,
                        invariants=invs,
                        policies=pols,
                    )
                )

        return bindings


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

    GO_FN_REGEX = re.compile(r"^func\s+(?:\([^)]+\)\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", re.MULTILINE)
    RUST_FN_REGEX = re.compile(r"^[ \t]*(?:pub\s+)?(?:async\s+)?fn\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", re.MULTILINE)

    def extract_symbols(self, file_path: Path | str, content: str | None = None) -> list[CodeSymbol]:
        p = Path(file_path)
        if content is None:
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                return []

        rel_path = str(p)
        symbols: list[CodeSymbol] = []

        # 1. Pure Python via stdlib ast
        if p.suffix == ".py":
            try:
                tree = ast.parse(content, filename=rel_path)
                for node in ast.iter_child_nodes(tree):
                    if isinstance(node, ast.ClassDef):
                        symbols.append(
                            CodeSymbol(
                                name=node.name,
                                kind="class",
                                file_path=rel_path,
                                line_start=node.lineno,
                                line_end=getattr(node, "end_lineno", node.lineno),
                                docstring=ast.get_docstring(node),
                                signature=_python_node_signature(node),
                            )
                        )
                        for item in node.body:
                            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                symbols.append(
                                    CodeSymbol(
                                        name=item.name,
                                        kind="method",
                                        file_path=rel_path,
                                        line_start=item.lineno,
                                        line_end=getattr(item, "end_lineno", item.lineno),
                                        parent_symbol=node.name,
                                        docstring=ast.get_docstring(item),
                                        signature=_python_node_signature(item),
                                    )
                                )
                    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        symbols.append(
                            CodeSymbol(
                                name=node.name,
                                kind="function",
                                file_path=rel_path,
                                line_start=node.lineno,
                                line_end=getattr(node, "end_lineno", node.lineno),
                                docstring=ast.get_docstring(node),
                                signature=_python_node_signature(node),
                            )
                        )
                return symbols
            except Exception:
                # Fallback to regex if Python code has syntax errors
                pass

        # 2. Regex fallback for polyglot files
        lines = content.splitlines()
        for idx, line in enumerate(lines, start=1):
            sig = _extract_header_signature(line)
            if p.suffix in (".ts", ".tsx", ".js", ".jsx"):
                for m in self.TS_CLS_REGEX.finditer(line):
                    symbols.append(
                        CodeSymbol(
                            name=m.group(1),
                            kind="class",
                            file_path=rel_path,
                            line_start=idx,
                            line_end=idx,
                            signature=sig,
                        )
                    )
                for m in self.TS_FN_REGEX.finditer(line):
                    symbols.append(
                        CodeSymbol(
                            name=m.group(1),
                            kind="function",
                            file_path=rel_path,
                            line_start=idx,
                            line_end=idx,
                            signature=sig,
                        )
                    )
            elif p.suffix == ".go":
                for m in self.GO_FN_REGEX.finditer(line):
                    symbols.append(
                        CodeSymbol(
                            name=m.group(1),
                            kind="function",
                            file_path=rel_path,
                            line_start=idx,
                            line_end=idx,
                            signature=sig,
                        )
                    )
            elif p.suffix == ".rs":
                for m in self.RUST_FN_REGEX.finditer(line):
                    symbols.append(
                        CodeSymbol(
                            name=m.group(1),
                            kind="function",
                            file_path=rel_path,
                            line_start=idx,
                            line_end=idx,
                            signature=sig,
                        )
                    )

        return symbols

    def list_test_functions(self, file_path: Path | str, content: str | None = None) -> dict[str, str]:
        p = Path(file_path)
        if content is None:
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                return {}

        rel_path = str(p)
        result: dict[str, str] = {}

        if p.suffix == ".py":
            try:
                tree = ast.parse(content, filename=rel_path)
                lines = content.splitlines()
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                        node.name.startswith("test_") or node.name.endswith("_test")
                    ):
                        start = (
                            min(d.lineno for d in node.decorator_list)
                            if getattr(node, "decorator_list", None)
                            else node.lineno
                        )
                        end = getattr(node, "end_lineno", node.lineno)
                        prefix = _extract_preceding_context(lines, start)
                        body = "\n".join(lines[start - 1 : end])
                        full_text = f"{prefix}\n{body}" if prefix else body
                        result[node.name] = full_text
                return result
            except Exception:
                pass

        test_fn_pattern = re.compile(
            r"(?:def\s+|function\s+|fn\s+|func\s+|void\s+|it\s*\(\s*['\"`]|test\s*\(\s*['\"`])([a-zA-Z0-9_\s-]+)"
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
                    result[current_test] = f"{prefix}\n{chunk}" if prefix else chunk
                current_test = m.group(1).strip()
                current_start = idx
                collected_lines = [line]
            elif current_test:
                collected_lines.append(line)

        if current_test:
            prefix = _extract_preceding_context(lines, current_start)
            chunk = "\n".join(collected_lines)
            result[current_test] = f"{prefix}\n{chunk}" if prefix else chunk

        return result

    def extract_test_bindings(self, file_path: Path | str, content: str | None = None) -> list[TestFunctionBinding]:
        p = Path(file_path)
        if content is None:
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                return []

        rel_path = str(p)
        bindings: list[TestFunctionBinding] = []

        # Python via stdlib ast
        if p.suffix == ".py":
            try:
                tree = ast.parse(content, filename=rel_path)
                lines = content.splitlines()
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                        node.name.startswith("test_") or node.name.endswith("_test")
                    ):
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
                            bindings.append(
                                TestFunctionBinding(
                                    test_file=rel_path,
                                    test_name=node.name,
                                    line_start=start,
                                    line_end=end,
                                        invariants=invs,
                                        policies=pols,
                                    )
                                )
                return bindings
            except Exception:
                pass

        # Regex fallback for tests across languages
        test_fn_pattern = re.compile(
            r"(?:def\s+|function\s+|fn\s+|func\s+|void\s+|it\s*\(\s*['\"`]|test\s*\(\s*['\"`])([a-zA-Z0-9_\s-]+)"
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
                        bindings.append(
                            TestFunctionBinding(
                                test_file=rel_path,
                                test_name=current_test,
                                line_start=current_start,
                                line_end=idx - 1,
                                invariants=invs,
                                policies=pols,
                            )
                        )
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
                bindings.append(
                    TestFunctionBinding(
                        test_file=rel_path,
                        test_name=current_test,
                        line_start=current_start,
                        line_end=len(lines),
                        invariants=invs,
                        policies=pols,
                    )
                )

        return bindings


def _is_public_symbol(sym: CodeSymbol) -> bool:
    """Return True if a code symbol represents a public contract/surface."""
    if sym.parent_symbol and sym.parent_symbol.startswith("_"):
        return False
    if sym.kind == "method":
        return not sym.name.startswith("_") or sym.name in ("__init__", "__call__")
    return not sym.name.startswith("_")


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

    code_exts = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java"}
    suffix = p.suffix.lower()
    if suffix not in code_exts:
        return True, [f"modified '{p.name}'"]

    # For Python, use FallbackIndexer (stdlib ast) for canonical ast.unparse signatures;
    # for other languages, use get_ast_indexer().
    indexer: BaseAstIndexer = FallbackIndexer() if suffix == ".py" else get_ast_indexer()
    old_symbols = [s for s in indexer.extract_symbols(p, old_content) if _is_public_symbol(s)]
    new_symbols = [s for s in indexer.extract_symbols(p, new_content) if _is_public_symbol(s)]

    old_map = {(s.kind, s.parent_symbol or "", s.name): (s.signature or "") for s in old_symbols}
    new_map = {(s.kind, s.parent_symbol or "", s.name): (s.signature or "") for s in new_symbols}

    reasons: list[str] = []
    for key in sorted(set(new_map) - set(old_map)):
        kind, parent, name = key
        qual = f"{parent}.{name}" if parent else name
        reasons.append(f"added {kind} '{qual}'")

    for key in sorted(set(old_map) - set(new_map)):
        kind, parent, name = key
        qual = f"{parent}.{name}" if parent else name
        reasons.append(f"removed {kind} '{qual}'")

    for key in sorted(set(old_map) & set(new_map)):
        if old_map[key] != new_map[key]:
            kind, parent, name = key
            qual = f"{parent}.{name}" if parent else name
            reasons.append(f"changed signature of {kind} '{qual}'")

    return bool(reasons), reasons


def is_ast_grep_available() -> bool:
    """Check if the high-performance ast-grep engine is installed in the current environment."""
    return AST_GREP_AVAILABLE


def get_ast_indexer(force_fallback: bool = False) -> BaseAstIndexer:
    """Factory retrieving the optimal AST indexer (AstGrepIndexer or FallbackIndexer)."""
    if AST_GREP_AVAILABLE and not force_fallback:
        return AstGrepIndexer()
    return FallbackIndexer()

