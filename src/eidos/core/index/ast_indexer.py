"""Polyglot AST Symbol Indexer and Structural AI Code Graph Engine.

Key features:
- High-performance polyglot AST parsing via ast-grep (ast-grep-py).
- Granular reverse code-to-spec symbol mapping (classes, functions, methods, interfaces).
- Syntax-aware test function extraction for capability invariants (INV-*) and policies (POL-*).
- Token-budgeted structural repo map generation for AI coding assistants.
"""

from __future__ import annotations

import inspect
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from ast_grep_py import SgRoot

INVARIANT_PATTERN = re.compile(r"\b(INV-[A-Z0-9_-]+)\b")
POLICY_PATTERN = re.compile(r"\b(POL-[A-Z0-9_-]+)\b")
_RUST_TEST_ATTR_RE = re.compile(r"#\[\s*(?:[A-Za-z_][A-Za-z0-9_]*\s*::\s*)*(?:test|rstest|test_case)\b")
_SCRIPT_BLOCK_RE = re.compile(r"<script\b[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE)
_RUST_LIFETIME_AT_START_RE = re.compile(r"^'[A-Za-z_][A-Za-z0-9_]*(?![A-Za-z0-9_'-])")


def _extract_sfc_script_blocks(content: str) -> list[tuple[str, int]]:
    """Extract (script_body, line_offset_0based) blocks from a .vue or .svelte single-file component."""
    blocks = [(m.group(1), content[: m.start(1)].count("\n")) for m in _SCRIPT_BLOCK_RE.finditer(content)]
    return blocks if blocks else [(content, 0)]


def _find_body_brace_outside_parens(text: str) -> int:
    """Return index of first '{' at paren_depth == 0 (outside parentheses and quotes), or -1."""
    if "{" not in text:
        return -1
    paren_depth = 0
    in_quote: str | None = None
    escaped = False
    for idx, ch in enumerate(text):
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if in_quote is not None:
            if ch == in_quote:
                in_quote = None
            continue
        if ch in ("'", '"', "`"):
            # Do not treat Rust lifetime annotations (e.g. <'a>, &'a str) as opening a single-quoted string
            if ch == "'" and _RUST_LIFETIME_AT_START_RE.match(text[idx:]):
                continue
            in_quote = ch
            continue
        if ch == "(":
            paren_depth += 1
        elif ch == ")" and paren_depth > 0:
            paren_depth -= 1
        elif ch == "{" and paren_depth == 0:
            return idx
    return -1


_DECL_KEYWORD_RE = re.compile(r"\b(?:class|interface|struct|enum|trait|type|function|func|fn|fun|def)\b")


def _extract_header_signature(node_text: str) -> str:
    """Extract normalized declaration header signature (before body block)."""
    first_lines: list[str] = []
    paren_depth = 0
    head_slice = node_text[:2048] if len(node_text) > 2048 else node_text
    for line in head_slice.splitlines():
        stripped = line.strip()
        if not stripped or (stripped.startswith(("#", "//", "/*", "*")) and not stripped.startswith("#[")):
            continue
        first_lines.append(stripped)
        paren_depth += stripped.count("(") - stripped.count(")")
        is_pure_deco_line = (
            (stripped.startswith("@") and not stripped.startswith("@interface")) or stripped.startswith("#[")
        ) and not _DECL_KEYWORD_RE.search(stripped)
        if (
            paren_depth <= 0
            and not is_pure_deco_line
            and (stripped.endswith(":") or _find_body_brace_outside_parens(stripped) != -1)
        ):
            break
        if len(first_lines) >= 60:
            break
    header = " ".join(first_lines)
    # Strip trailing body start outside parentheses
    brace_idx = _find_body_brace_outside_parens(header)
    if brace_idx != -1:
        header = header[:brace_idx].strip()
    elif header.endswith(":"):
        header = header[:-1].strip()
    header = re.sub(r"\s+", " ", header)
    header = re.sub(r"\(\s+", "(", header)
    header = re.sub(r",\s*\)", ")", header)
    return re.sub(r"\s+\)", ")", header)


def _extract_preceding_context(lines: list[str], start_line_1based: int) -> str:
    """Collect decorators, attributes, and comments immediately preceding a function definition."""
    collected: list[str] = []
    idx = start_line_1based - 2  # line before start_line_1based (0-indexed)
    while idx >= 0:
        stripped = lines[idx].strip()
        if stripped.startswith(("#", "//", "/*", "*", "*/", "@", "#[")) or (
            stripped == "" and collected and collected[-1].startswith(("@", "#["))
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

        if suffix == ".py":
            try:
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
            except (RuntimeError, ValueError):
                pass
            return imports

        if suffix in (".ts", ".tsx", ".js", ".jsx", ".vue", ".svelte"):
            ts_lang = (
                "tsx"
                if suffix in (".tsx", ".jsx", ".vue", ".svelte")
                else ("typescript" if suffix == ".ts" else "javascript")
            )
            blocks = _extract_sfc_script_blocks(content) if suffix in (".vue", ".svelte") else [(content, 0)]
            for block_text, _ in blocks:
                try:
                    ts_root = SgRoot(block_text, ts_lang).root()
                except (RuntimeError, ValueError):
                    continue
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
            try:
                go_root = SgRoot(content, "go").root()
                for spec in go_root.find_all(kind="import_spec"):
                    path_node = spec.field("path")
                    if path_node is not None:
                        _add(path_node.text().strip('"`'))
            except (RuntimeError, ValueError):
                pass
            return imports

        if suffix == ".rs":
            try:
                rs_root = SgRoot(content, "rust").root()
                for udecl in rs_root.find_all(kind="use_declaration"):
                    arg = udecl.field("argument")
                    if arg is not None:
                        m = re.match(r"^([A-Za-z0-9_:]+)", arg.text().strip())
                        if m:
                            clean_rs = m.group(1).rstrip(":")
                            if clean_rs:
                                _add(clean_rs)
            except (RuntimeError, ValueError):
                pass
            return imports

        if suffix == ".java":
            try:
                java_root = SgRoot(content, "java").root()
                for idecl in java_root.find_all(kind="import_declaration"):
                    has_wildcard = any(ch.kind() == "asterisk" for ch in idecl.children())
                    target = next(
                        (ch.text().strip() for ch in idecl.children() if ch.kind() in ("scoped_identifier", "identifier")),
                        "",
                    )
                    if target:
                        _add(f"{target}.*" if has_wildcard else target)
            except (RuntimeError, ValueError):
                pass
            return imports

        if suffix in (".kt", ".kts"):
            try:
                kt_root = SgRoot(content, "kotlin").root()
                for ihdr in kt_root.find_all(kind="import_header"):
                    txt = re.sub(r"^import\s+", "", ihdr.text().strip())
                    txt = txt.split(" as ", 1)[0].strip().rstrip(";")
                    if txt:
                        _add(txt)
            except (RuntimeError, ValueError):
                pass
            return imports

        if suffix in (".c", ".cpp", ".h", ".hpp"):
            try:
                cpp_root = SgRoot(content, "cpp").root()
                for inc in cpp_root.find_all(kind="preproc_include"):
                    path_node = inc.field("path")
                    if path_node is not None:
                        _add(path_node.text().strip('<>"'))
            except (RuntimeError, ValueError):
                pass
            return imports

        if suffix == ".cs":
            try:
                cs_root = SgRoot(content, "csharp").root()
                for udir in cs_root.find_all(kind="using_directive"):
                    targets = [ch.text().strip() for ch in udir.children() if ch.kind() in ("qualified_name", "identifier")]
                    if targets:
                        _add(targets[-1])
            except (RuntimeError, ValueError):
                pass
            return imports

        if suffix == ".rb":
            try:
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
            except (RuntimeError, ValueError):
                pass
            return imports

        if suffix == ".php":
            try:
                php_root = SgRoot(content, "php").root()
                for udecl in php_root.find_all(kind="namespace_use_declaration"):
                    prefix_ns = next((ch.text().strip() for ch in udecl.children() if ch.kind() == "namespace_name"), "")
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
            except (RuntimeError, ValueError):
                pass
            return imports

        if suffix == ".swift":
            try:
                sw_root = SgRoot(content, "swift").root()
                for idecl in sw_root.find_all(kind="import_declaration"):
                    target = next(
                        (ch.text().strip() for ch in idecl.children() if ch.kind() in ("identifier", "simple_identifier")),
                        "",
                    )
                    if target:
                        _add(target)
            except (RuntimeError, ValueError):
                pass
            return imports

        return imports

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
        ".js": "javascript",
        ".jsx": "jsx",
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
        if lang in ("c", "cpp") and node.kind() in ("function_definition", "declaration", "field_declaration"):
            fdecl = node.field("declarator")
            while fdecl is not None and fdecl.kind() != "function_declarator":
                fdecl = fdecl.field("declarator") or next(
                    (c for c in fdecl.children() if c.kind() == "function_declarator"),
                    None,
                )
            if fdecl is not None:
                inner = fdecl.field("declarator")
                while inner is not None and inner.kind() not in (
                    "identifier",
                    "field_identifier",
                    "qualified_identifier",
                    "destructor_name",
                ):
                    inner = inner.field("declarator") or next(
                        (
                            c
                            for c in inner.children()
                            if c.kind() in ("identifier", "field_identifier", "qualified_identifier")
                        ),
                        None,
                    )
                if inner is not None:
                    name_txt = inner.text().split("::")[-1].strip()
                    return name_txt if name_txt and name_txt not in AstGrepIndexer._C_CONTROL_KEYWORDS else None
            return None
        name_field = node.field("name")
        if name_field is not None:
            return name_field.text()
        for child in node.children():
            if child.kind() in ("type_identifier", "simple_identifier", "identifier", "constant", "name"):
                return child.text()
        return None

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
        """Extract test title from TS/JS it/test/describe (.each/.concurrent/.only/.skip) and Deno.test call_expression."""
        fn_ident = c.field("function")
        if fn_ident is None:
            return None
        fn_text = re.sub(r"\s+", " ", fn_ident.text().strip())
        is_deno = fn_text == "Deno.test"
        is_direct_or_member = (
            fn_text in ("it", "test", "describe")
            or is_deno
            or bool(
                re.match(
                    r"^(?:it|test|describe)\.(?:only|skip|concurrent|todo|serial|fails|fixme)(?:\.(?:only|skip|concurrent))?$",
                    fn_text,
                )
            )
        )
        is_parameterized_each = bool(re.match(r"^(?:it|test|describe)(?:\.[A-Za-z_]+)*\.each\s*(?:\(|`)", fn_text))
        if not (is_direct_or_member or is_parameterized_each):
            return None

        args = c.field("arguments")
        if args is None:
            return None
        first_arg = next(
            (ch for ch in args.children() if ch.text() not in ("(", ")", ",")),
            None,
        )
        if first_arg is None:
            return None
        raw_arg = first_arg.text().strip()
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
        try:
            root = SgRoot(content, lang)
            node = root.root()
        except (RuntimeError, ValueError):
            return []

        symbols: list[CodeSymbol] = []
        content_lines = content.splitlines()
        py_dunder_all = _extract_python_dunder_all_from_source(content, py_root=node) if lang == "python" else None
        ts_exports = (
            _extract_ts_exported_names(content, ts_root=node)
            if lang in ("typescript", "tsx", "javascript", "jsx")
            else None
        )

        def _node_header_sig(ast_node: Any, rng_obj: Any) -> str:
            s_ln = rng_obj.start.line
            s_col = rng_obj.start.column
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
                return _extract_header_signature(ast_node.text())
            slice_lines = content_lines[s_ln : min(e_ln + 1, s_ln + 60)]
            if not slice_lines:
                return _extract_header_signature(ast_node.text())
            first = slice_lines[0][s_col:]
            return _extract_header_signature("\n".join([first, *slice_lines[1:]]))

        all_fn_kinds = frozenset(
            {
                "function_definition",
                "function_declaration",
                "method_definition",
                "function_item",
                "method_declaration",
                "method",
                "singleton_method",
                "local_function_statement",
            }
        )

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
                            "struct"
                            if ck == "struct_specifier"
                            else ("enum" if ck == "enum_specifier" else "class")
                        )
                        sig = hdr_sig
                    elif lang == "csharp":
                        kind_label = (
                            "interface"
                            if ck == "interface_declaration"
                            else ("struct" if ck == "struct_declaration" else ("enum" if ck == "enum_declaration" else "class"))
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
                    if lang in ("typescript", "tsx", "javascript", "jsx"):
                        parent = m.parent()
                        if parent is not None and parent.kind() == "export_statement" and not sig.startswith("export "):
                            sig = f"export {sig}"
                    if dec_list:
                        missing_decos = [d for d in dec_list if not sig.startswith(d)]
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
            "c": ["function_definition", "declaration"],
            "cpp": ["function_definition", "declaration", "field_declaration"],
            "csharp": ["method_declaration"],
            "ruby": ["method", "singleton_method"],
            "php": ["function_definition", "method_declaration"],
            "swift": ["function_declaration"],
        }.get(lang, [])

        class_ranges = [
            (cname, cnode.range().start.line, cnode.range().end.line) for cname, cnode in class_nodes.items()
        ]

        for fk in fn_kinds:
            for m in node.find_all(kind=fk):
                if (
                    lang in ("c", "cpp")
                    and fk in ("declaration", "field_declaration")
                    and not list(m.find_all(kind="function_declarator"))
                ):
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
                sig = _node_header_sig(m, rng)
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
                    if parent is not None and parent.kind() == "export_statement" and not sig.startswith("export "):
                        sig = f"export {sig}"
                if dec_list:
                    missing_decos = [d for d in dec_list if not sig.startswith(d)]
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
                )
                sym.is_boundary_export = _evaluate_boundary_export(sym, py_dunder_all=py_dunder_all)
                symbols.append(sym)

        if lang in ("typescript", "tsx", "javascript", "jsx"):
            seen_fn_names = {s.name for s in symbols if s.kind == "function" and not s.parent_symbol}
            fn_like_kinds = all_fn_kinds | {
                "arrow_function",
                "function_expression",
                "function",
                "generator_function",
            }
            for vdecl in node.find_all(kind="variable_declarator"):
                val_node = vdecl.field("value")
                if val_node is None or val_node.kind() not in (
                    "arrow_function",
                    "function_expression",
                    "function",
                    "generator_function",
                ):
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
                    if akind in ("lexical_declaration", "variable_declaration"):
                        stmt_node = anc
                    elif akind == "export_statement":
                        stmt_node = anc
                        is_exported_decl = True
                    anc = anc.parent()
                if nested_in_fn_or_cls:
                    continue
                fname = self._node_name(vdecl, lang)
                if not fname or fname in seen_fn_names:
                    continue
                seen_fn_names.add(fname)
                rng = stmt_node.range()
                sig = _node_header_sig(stmt_node, rng)
                if is_exported_decl and not sig.startswith("export "):
                    sig = f"export {sig}"
                sym = CodeSymbol(
                    name=fname,
                    kind="function",
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
            try:
                root = SgRoot(block_text, sg_lang)
                node = root.root()
            except (RuntimeError, ValueError):
                continue

            lines = block_text.splitlines()
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
                    or "[Fact]" in full_text
                    or "[Theory]" in full_text
                    or "[Test]" in full_text
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


_RUST_PUB_RE = re.compile(
    r"(?:^|\s)pub(?:\s*\([^)]*\))?\s+(?:async\s+|unsafe\s+|const\s+|extern\s+(?:\"[^\"]*\"\s+)?)?(?:fn|struct|enum|trait|type)\b"
)
_JAVA_KT_PRIVATE_RE = re.compile(r"(?:^|\s)(?:private|internal)\s+")
_TS_PRIVATE_METHOD_RE = re.compile(r"^(?:private\s+|protected\s+|#)")


def _strip_leading_decorators_from_signature(sig: str) -> str:
    """Strip leading @decorator(...) / #[attribute] prefixes from a signature string before visibility analysis."""
    if not sig or not sig.startswith(("@", "#[", "#![")):
        return sig
    s = sig
    while s:
        if s.startswith(("#[", "#![")):
            depth = 0
            in_q: str | None = None
            esc = False
            end_idx = -1
            for i, ch in enumerate(s):
                if esc:
                    esc = False
                    continue
                if ch == "\\":
                    esc = True
                    continue
                if in_q is not None:
                    if ch == in_q:
                        in_q = None
                    continue
                if ch in ("'", '"', "`"):
                    in_q = ch
                    continue
                if ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
                    if depth == 0:
                        end_idx = i
                        break
            if end_idx == -1:
                break
            s = s[end_idx + 1 :].lstrip()
            continue
        if s.startswith("@"):
            m = re.match(r"^@[A-Za-z_$][A-Za-z0-9_$.]*", s)
            if not m:
                break
            rest = s[m.end() :].lstrip()
            if rest.startswith("("):
                depth = 0
                in_q = None
                esc = False
                end_idx = -1
                for i, ch in enumerate(rest):
                    if esc:
                        esc = False
                        continue
                    if ch == "\\":
                        esc = True
                        continue
                    if in_q is not None:
                        if ch == in_q:
                            in_q = None
                        continue
                    if ch in ("'", '"', "`"):
                        in_q = ch
                        continue
                    if ch == "(":
                        depth += 1
                    elif ch == ")":
                        depth -= 1
                        if depth == 0:
                            end_idx = i
                            break
                if end_idx == -1:
                    break
                s = rest[end_idx + 1 :].lstrip()
            else:
                s = rest
            continue
        break
    return s


_DUNDER_ALL_ASSIGN_RE = re.compile(r"^[ \t]*__all__\s*(?::[^=\n]+)?\+?=", re.MULTILINE)


def _extract_python_dunder_all_from_source(content: str, py_root: Any = None) -> set[str] | None:
    """Return explicitly declared __all__ symbol names if defined at Python module top level, else None."""
    if "__all__" not in content or not _DUNDER_ALL_ASSIGN_RE.search(content):
        return None
    if py_root is None:
        try:
            py_root = SgRoot(content, "python").root()
        except (RuntimeError, ValueError):
            return None
    names: set[str] = set()
    found = False
    assign_kinds = ("assignment", "augmented_assignment")
    for stmt in py_root.children():
        assigns = [stmt] if stmt.kind() in assign_kinds else [c for c in stmt.children() if c.kind() in assign_kinds]
        for assign in assigns:
            left = assign.field("left")
            if left is not None and left.text().strip() == "__all__":
                found = True
                right = assign.field("right")
                if right is not None and right.kind() in ("list", "tuple", "set"):
                    for elt in right.children():
                        if elt.kind() == "string":
                            sc = "".join(c.text() for c in elt.children() if c.kind() == "string_content")
                            names.add(sc)
    return names if found else None


def _extract_ts_exported_names(content: str, ts_root: Any = None, lang: str = "tsx") -> set[str]:
    """Return all identifier names exported via export declarations, export lists, or export default via SgRoot."""
    if ts_root is None:
        if "export" not in content:
            return set()
        try:
            ts_root = SgRoot(content, lang).root()
        except (RuntimeError, ValueError):
            return set()
    exports: set[str] = set()
    for exp in ts_root.find_all(kind="export_statement"):
        for ch in exp.children():
            ck = ch.kind()
            if ck in (
                "function_declaration",
                "generator_function_declaration",
                "class_declaration",
                "abstract_class_declaration",
                "interface_declaration",
                "type_alias_declaration",
                "enum_declaration",
            ):
                n = AstGrepIndexer._node_name(ch, lang)
                if n:
                    exports.add(n)
            elif ck in ("lexical_declaration", "variable_declaration"):
                for vd in ch.find_all(kind="variable_declarator"):
                    n = AstGrepIndexer._node_name(vd, lang)
                    if n:
                        exports.add(n)
            elif ck == "export_clause":
                for spec in ch.find_all(kind="export_specifier"):
                    for id_node in spec.children():
                        if id_node.kind() in ("identifier", "type_identifier", "property_identifier"):
                            exports.add(id_node.text().strip())
            elif ck == "namespace_export":
                for id_node in ch.children():
                    if id_node.kind() in ("identifier", "type_identifier"):
                        exports.add(id_node.text().strip())
            elif ck in ("identifier", "type_identifier"):
                exports.add(ch.text().strip())
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
    raw_sig = (sym.signature or "").strip()
    sig = _strip_leading_decorators_from_signature(raw_sig)

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


def public_symbol_map(
    content: str | None,
    file_path: Path | str,
    *,
    _symbols: list[CodeSymbol] | None = None,
) -> dict[tuple[str, str, str], str]:
    """Map ``(kind, parent_symbol, name) -> signature`` of the public symbols in one file version.

    ``None`` content (absent file) and files without AST support yield an empty map.
    """
    p = Path(file_path)
    suffix = p.suffix.lower()
    if content is None or suffix not in _AST_DIFF_EXTS:
        return {}
    syms = _symbols if _symbols is not None else get_ast_indexer().extract_symbols(p, content)
    return {(s.kind, s.parent_symbol or "", s.name): (s.signature or "") for s in syms if _is_public_symbol(s)}


def boundary_symbol_map(
    content: str | None,
    file_path: Path | str,
    *,
    _symbols: list[CodeSymbol] | None = None,
) -> dict[tuple[str, str, str], str]:
    """Map ``(kind, parent_symbol, name) -> signature`` of package/contract boundary symbols in one file version (DLT-2026-081)."""
    p = Path(file_path)
    suffix = p.suffix.lower()
    if content is None or suffix not in _AST_DIFF_EXTS:
        return {}
    py_dunder_all: set[str] | None = None
    reexported: dict[tuple[str, str, str], str] = {}
    fname_lower = p.name.lower()
    if suffix == ".py" and (fname_lower == "__init__.py" or _symbols is None):
        try:
            py_root = SgRoot(content, "python").root()
            py_dunder_all = _extract_python_dunder_all_from_source(content, py_root=py_root)
            if fname_lower == "__init__.py":
                for stmt in py_root.children():
                    if stmt.kind() == "import_from_statement":
                        past_import_kw = False
                        for ch in stmt.children():
                            if ch.kind() == "import":
                                past_import_kw = True
                            elif past_import_kw:
                                bound_name = ""
                                if ch.kind() == "dotted_name":
                                    bound_name = ch.text().strip()
                                elif ch.kind() == "aliased_import":
                                    alias_node = ch.field("alias") or ch.field("name")
                                    if alias_node is not None:
                                        bound_name = alias_node.text().strip()
                                if (
                                    bound_name
                                    and bound_name != "*"
                                    and not bound_name.startswith("_")
                                    and (py_dunder_all is None or bound_name in py_dunder_all)
                                ):
                                    reexported[("export", "", bound_name)] = f"reexport {bound_name}"
        except (RuntimeError, ValueError):
            py_dunder_all = None
    elif fname_lower in ("index.ts", "index.tsx", "index.js", "index.jsx"):
        ts_lang = "tsx" if suffix in (".tsx", ".jsx") else ("typescript" if suffix == ".ts" else "javascript")
        for name in _extract_ts_exported_names(content, lang=ts_lang):
            if not name.startswith("_"):
                reexported[("export", "", name)] = f"reexport {name}"
    elif fname_lower in ("mod.rs", "lib.rs"):
        try:
            rs_root = SgRoot(content, "rust").root()
            for udecl in rs_root.find_all(kind="use_declaration"):
                if not any(ch.kind() == "visibility_modifier" for ch in udecl.children()):
                    continue
                arg = udecl.field("argument")
                if arg is None:
                    continue
                if arg.kind() == "scoped_identifier":
                    name_node = arg.field("name")
                    if name_node is not None:
                        rname = name_node.text().strip()
                        if rname and rname != "*":
                            reexported[("export", "", rname)] = f"pub use {rname}"
                elif arg.kind() == "use_as_clause":
                    alias_node = arg.field("alias")
                    if alias_node is not None:
                        rname = alias_node.text().strip()
                        if rname:
                            reexported[("export", "", rname)] = f"pub use {rname}"
                else:
                    for ul in arg.find_all(kind="use_list"):
                        for item in ul.children():
                            if item.kind() == "identifier":
                                rname = item.text().strip()
                                if rname and rname != "self":
                                    reexported[("export", "", rname)] = f"pub use {rname}"
                            elif item.kind() == "use_as_clause":
                                alias_node = item.field("alias")
                                if alias_node is not None:
                                    rname = alias_node.text().strip()
                                    if rname:
                                        reexported[("export", "", rname)] = f"pub use {rname}"
                            elif item.kind() == "scoped_identifier":
                                name_node = item.field("name")
                                if name_node is not None:
                                    rname = name_node.text().strip()
                                    if rname and rname != "self":
                                        reexported[("export", "", rname)] = f"pub use {rname}"
        except (RuntimeError, ValueError):
            pass

    syms = _symbols if _symbols is not None else get_ast_indexer().extract_symbols(p, content)
    out: dict[tuple[str, str, str], str] = {
        (s.kind, s.parent_symbol or "", s.name): (s.signature or "")
        for s in syms
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
    p = Path(file_path)
    suffix = p.suffix.lower()
    if suffix not in _AST_DIFF_EXTS:
        return PublicSymbolDiff()
    indexer = get_ast_indexer()
    old_syms = indexer.extract_symbols(p, old_content) if old_content is not None else []
    new_syms = indexer.extract_symbols(p, new_content) if new_content is not None else []
    return diff_symbol_maps(
        public_symbol_map(old_content, p, _symbols=old_syms),
        public_symbol_map(new_content, p, _symbols=new_syms),
        old_boundary_map=boundary_symbol_map(old_content, p, _symbols=old_syms),
        new_boundary_map=boundary_symbol_map(new_content, p, _symbols=new_syms),
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
    exports: set[str] = set()
    props: set[str] = set()
    blocks = _extract_sfc_script_blocks(content) if suffix in (".vue", ".svelte") else [(content, 0)]
    ts_lang = "tsx" if suffix in (".tsx", ".jsx", ".vue", ".svelte") else ("typescript" if suffix == ".ts" else "javascript")
    if suffix != ".html":
        for block_text, _ in blocks:
            try:
                ts_root = SgRoot(block_text, ts_lang).root()
            except (RuntimeError, ValueError):
                continue
            exports.update(_extract_ts_exported_names(block_text, ts_root=ts_root, lang=ts_lang))
            for k in ("interface_declaration", "type_alias_declaration"):
                for decl in ts_root.find_all(kind=k):
                    pname = AstGrepIndexer._node_name(decl, ts_lang)
                    if pname and pname.endswith("Props"):
                        body_node = decl.field("body") or decl.field("value")
                        if body_node is not None:
                            props.add(f"{pname}:{_normalize_ws(body_node.text())}")

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


def get_ast_indexer() -> BaseAstIndexer:
    """Factory retrieving the unified polyglot AST indexer (AstGrepIndexer)."""
    return AstGrepIndexer()
