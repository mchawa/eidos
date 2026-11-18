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

import orjson
from ast_grep_py import SgRoot

INVARIANT_PATTERN = re.compile(r"(?<![A-Z0-9_-])(INV-[A-Z0-9]+(?:-[A-Z0-9]+)*)(?![A-Z0-9_]|-[A-Z0-9])")
POLICY_PATTERN = re.compile(r"(?<![A-Z0-9_-])(POL-[A-Z0-9]+(?:-[A-Z0-9]+)*)(?![A-Z0-9_]|-[A-Z0-9])")
INV_OR_POL_TOKEN_RE = re.compile(r"(?<![A-Z0-9_-])((?:INV|POL)-[A-Z0-9]+(?:-[A-Z0-9]+)*)(?![A-Z0-9_]|-[A-Z0-9])")
_RUST_TEST_ATTR_RE = re.compile(r"#\[\s*(?:[A-Za-z_][A-Za-z0-9_]*\s*::\s*)*(?:test|rstest|test_case)\b")
_SCRIPT_BLOCK_RE = re.compile(r"<script\b[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE)
_RUST_LIFETIME_AT_START_RE = re.compile(r"^'[A-Za-z_][A-Za-z0-9_]*(?![A-Za-z0-9_'-])")
_CPP_EXPORT_MACRO_TOKEN_RE = re.compile(
    r"\b(?:"
    r"[A-Z_][A-Z0-9_]*_(?:API|EXPORT|EXPORTS|IMPORT|PUBLIC|LOCAL|DLL|VISIBLE|HIDDEN|CALL|CDECL|STDCALL|FASTCALL|VECTORCALL|FORCEINLINE|NOINLINE|INLINE|DEPRECATED|NODISCARD|NORETURN|ATTR)"
    r"|(?:API|EXPORT|DLL|VKAPI)_[A-Z0-9_]+"
    r"|DLL(?:EXPORT|IMPORT)|APIENTRY|WINGDIAPI|CALLBACK|WINAPI|FORCEINLINE|NOINLINE"
    r")\b(?:[ \t]*\([^()\n]*\))?(?=\s+[A-Za-z_~*:&\[])"
)
_CPP_TYPE_MACRO_HEADER_RE = re.compile(
    r"\b(?P<kw>class|struct|union|enum(?:\s+(?:class|struct))?)"
    r"(?P<mid>(?:\s+(?:[A-Z_][A-Z0-9_]*(?:[ \t]*\([^()\n]*\))?|\[\[[^\]\n]*\]\]|__declspec[ \t]*\([^()\n]*\)|alignas[ \t]*\([^()\n]*\)))+)"
    r"(?P<tail>\s+[A-Za-z_][A-Za-z0-9_]*\s*(?:final\b|<|:|\{|;))"
)
_CPP_UPPER_MACRO_IN_MID_RE = re.compile(r"\b[A-Z_][A-Z0-9_]*\b(?:[ \t]*\([^()\n]*\))?")


def _blank_span_preserving_newlines(m: re.Match[str]) -> str:
    txt = m.group(0)
    if "\n" not in txt and "\r" not in txt:
        return " " * (m.end() - m.start())
    return "".join(ch if ch in ("\n", "\r") else " " for ch in txt)


def _blank_cpp_export_macros(content: str) -> str:
    """Blank C/C++ declaration export/visibility macros with equal-length spaces to preserve AST line/column offsets."""
    res = _CPP_EXPORT_MACRO_TOKEN_RE.sub(_blank_span_preserving_newlines, content)

    def _replace_type_hdr(m: re.Match[str]) -> str:
        kw = m.group("kw")
        mid = m.group("mid")
        tail = m.group("tail")
        blanked_mid = _CPP_UPPER_MACRO_IN_MID_RE.sub(_blank_span_preserving_newlines, mid)
        return f"{kw}{blanked_mid}{tail}"

    return _CPP_TYPE_MACRO_HEADER_RE.sub(_replace_type_hdr, res)


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


_BLOCK_COMMENT_EXTS = frozenset({
    ".cs",
    ".csx",
    ".java",
    ".kt",
    ".kts",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".mts",
    ".cts",
    ".go",
    ".rs",
    ".c",
    ".h",
    ".cpp",
    ".cc",
    ".cxx",
    ".hpp",
    ".hh",
    ".hxx",
    ".swift",
    ".scala",
})
_PREPROC_IF_EXTS = frozenset({".cs", ".csx", ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"})
_SINGLE_QUOTE_STRING_EXTS = frozenset({".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"})
_RAW_TRIPLE_QUOTE_EXTS = frozenset({".cs", ".csx", ".java", ".kt", ".kts", ".scala", ".swift"})

_BLOCK_COMMENT_CODE_RE = re.compile(
    r'(?m)(?:"[^"\n]*"|(?<![A-Za-z])\'[^\'\n]*\'|^\s*(?:\[(?:Fact|Theory|Test\w*|DataTestMethod|Ignore|Skip)\b|\b(?:public|private|protected|internal|static|async)\s+(?:async\s+)?[\w<>\[\]]+\s+\w+\s*\(|\b(?:var|const|let|val)\s+\w+\s*=)|;\s*$)'
)
_LINE_COMMENT_CODE_RE = re.compile(
    r'(?:^\s*(?:\{|\[(?:Fact|Theory|Test\w*|DataTestMethod|Ignore|Skip)\b|\);|\],|"(?:[^"\\]|\\.)*"\s*,?\s*$|\b(?:var|const|let|val)\s+\w+\s*=|\b(?:if|for|foreach|while|switch|catch)\s*\(|\b(?:public|private|protected|internal|static|async)\s+[\w<>\[\]]+\s+\w+\s*\(|\b(?:return|throw|await)\b.+;|(?:Assert|assert)\s*[.(])|[)"\'\}\]\w]\s*;\s*$|(?:=|\b(?:var|const|let|val)\b|[({\[]|,)\s*(?:"[^"\n]*"|\'[^\'\n]*\')|(?:"[^"\n]*"|\'[^\'\n]*\')\s*(?:[,;)\]}]|:(?!\s*$|[^"]*$)))'
)


def _blank_preserving_newlines(text: str) -> str:
    """Replace non-newline characters with spaces so 1-based line numbers stay identical."""
    return re.sub(r"[^\r\n]", " ", text)


def _blank_disabled_preproc_blocks(content: str) -> str:
    """Blank ``#if false`` / ``#if 0`` preprocessor regions while preserving line endings."""
    stack: list[tuple[bool, bool, bool]] = []
    out: list[str] = []
    for line in content.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("#"):
            if m := re.match(r"^#\s*if\b(.*)$", stripped):
                cond = re.split(r"//|/\*", m.group(1), maxsplit=1)[0].strip()
                parent_dis = stack[-1][0] if stack else False
                explicit_false = cond in ("false", "0", "(false)", "(0)", "!true", "!1")
                stack.append((parent_dis or explicit_false, explicit_false, parent_dis))
                out.append(line)
                continue
            if m := re.match(r"^#\s*elif\b(.*)$", stripped):
                if stack:
                    _, _, parent_dis = stack.pop()
                    cond = re.split(r"//|/\*", m.group(1), maxsplit=1)[0].strip()
                    explicit_false = cond in ("false", "0", "(false)", "(0)", "!true", "!1")
                    stack.append((parent_dis or explicit_false, explicit_false, parent_dis))
                out.append(line)
                continue
            if re.match(r"^#\s*else\b", stripped):
                if stack:
                    cur_dis, explicit_false, parent_dis = stack.pop()
                    stack.append((parent_dis or (not explicit_false and cur_dis), False, parent_dis))
                out.append(line)
                continue
            if re.match(r"^#\s*endif\b", stripped):
                if stack:
                    stack.pop()
                out.append(line)
                continue
        if stack and stack[-1][0] and "eidos:fixture" not in line:
            out.append(_blank_preserving_newlines(line))
        else:
            out.append(line)
    return "".join(out)


def _looks_like_commented_out_code_block(body: str) -> bool:
    """Return True when a ``/* ... */`` body contains code syntax rather than pure prose."""
    return "{" in body or "}" in body or bool(_BLOCK_COMMENT_CODE_RE.search(body))


def _looks_like_commented_out_code_line(body: str) -> bool:
    """Return True when a single-line ``// ...`` body contains commented-out code."""
    stripped = body.strip()
    return bool(stripped) and (stripped in ("{", "}", "};", ");") or bool(_LINE_COMMENT_CODE_RE.search(stripped)))


def _strip_commented_out_code_blocks(content: str, file_path: Path | str) -> str:
    """Blank commented-out code blocks and ``#if false`` regions in C-family test files.

    Preserves line numbers, string literals (including glob patterns like ``"apps/*/Dockerfile"``
    and C# ``@"..."`` / ``$@"..."`` strings), Rust lifetimes, doc comments (``/**``, ``/*!``,
    ``///``), prose comments (e.g. ``/* INV-C-ARENA-001 */``), and ``eidos:fixture`` lines.
    """
    ext = Path(file_path).suffix.lower()
    if ext not in _BLOCK_COMMENT_EXTS:
        return content
    if "/*" not in content and "//" not in content and ("#if" not in content or ext not in _PREPROC_IF_EXTS):
        return content

    if ext in _PREPROC_IF_EXTS and "#if" in content:
        content = _blank_disabled_preproc_blocks(content)
    if "/*" not in content and "//" not in content:
        return content

    n = len(content)
    chunks: list[str] = []
    last = 0
    i = 0
    while i < n:
        ch = content[i]
        if ext in (".cs", ".csx") and content.startswith(('$@"', '@$"', '@"'), i):
            i += 3 if content[i] != "@" else 2
            while i < n:
                if content[i] == '"':
                    if i + 1 < n and content[i + 1] == '"':
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if ext in _RAW_TRIPLE_QUOTE_EXTS and content.startswith('"""', i):
            close = content.find('"""', i + 3)
            i = n if close == -1 else close + 3
            continue
        if ch == '"':
            i += 1
            while i < n:
                c = content[i]
                if c == "\\":
                    i += 2
                    continue
                if c == '"':
                    i += 1
                    break
                i += 1
            continue
        if ch == "`":
            i += 1
            while i < n:
                c = content[i]
                if c == "\\" and ext != ".go":
                    i += 2
                    continue
                if c == "`":
                    i += 1
                    break
                i += 1
            continue
        if ch == "'":
            if ext == ".rs" and _RUST_LIFETIME_AT_START_RE.match(content[i : i + 64]):
                i += 1
                continue
            i += 1
            while i < n:
                c = content[i]
                if c == "\\":
                    i += 2
                    continue
                if c == "'":
                    i += 1
                    break
                if c in ("\n", "\r") and ext not in _SINGLE_QUOTE_STRING_EXTS:
                    break
                i += 1
            continue
        if ch == "/" and i + 1 < n:
            nxt = content[i + 1]
            if nxt == "*":
                end = content.find("*/", i + 2)
                end_idx = n if end == -1 else end + 2
                raw = content[i:end_idx]
                is_doc = raw.startswith("/*!") or (
                    raw.startswith("/**") and len(raw) > 3 and raw[3] not in "*/"
                )
                body = raw[2:-2] if end != -1 else raw[2:]
                if "eidos:fixture" not in raw and not is_doc and _looks_like_commented_out_code_block(body):
                    chunks.append(content[last:i])
                    chunks.append(_blank_preserving_newlines(raw))
                    last = end_idx
                i = end_idx
                continue
            if nxt == "/":
                nl = content.find("\n", i + 2)
                end_idx = n if nl == -1 else nl
                raw = content[i:end_idx]
                is_doc = raw.startswith("///") and not raw.startswith("////")
                if "eidos:fixture" not in raw and not is_doc and _looks_like_commented_out_code_line(raw[2:]):
                    chunks.append(content[last:i])
                    chunks.append(_blank_preserving_newlines(raw))
                    last = end_idx
                i = end_idx
                continue
        i += 1

    if last == 0:
        return content
    chunks.append(content[last:])
    return "".join(chunks)


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
                        (
                            ch.text().strip()
                            for ch in idecl.children()
                            if ch.kind() in ("scoped_identifier", "identifier")
                        ),
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
                    targets = [
                        ch.text().strip() for ch in udir.children() if ch.kind() in ("qualified_name", "identifier")
                    ]
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
            except (RuntimeError, ValueError):
                pass
            return imports

        if suffix == ".swift":
            try:
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
            except (RuntimeError, ValueError):
                pass
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
                    (
                        ch.text().strip()
                        for ch in pkg.children()
                        if ch.kind() in ("identifier", "simple_identifier")
                    ),
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
        if lang == "csharp" and node.kind() == "indexer_declaration":
            return "this"
        if lang == "csharp" and node.kind() in ("event_field_declaration", "field_declaration"):
            for vdecl in node.find_all(kind="variable_declarator"):
                nf = vdecl.field("name")
                if nf is not None and nf.text().strip():
                    return nf.text().strip()
                for ch in vdecl.children():
                    if ch.kind() == "identifier" and ch.text().strip():
                        return ch.text().strip()
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
            if (
                parse_lines is not content_lines
                and s_ln < len(content_lines)
                and s_ln < len(parse_lines)
                and s_col > 0
            ):
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
                    elif fk in ("function_declaration", "method_definition") and ts_target_key in ts_overload_sig_targets:
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
                if fk in ("function_signature", "method_signature", "function_signature_item", "method_elem", "method_spec") or (
                    parent_cls in interface_or_trait_names and sig.endswith(";")
                ):
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
        return _extract_polyglot_http_endpoints(self, p, content, _symbols=_symbols)

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
        return _extract_polyglot_http_requests(self, p, content, _symbols=_symbols)


_RUST_PUB_RE = re.compile(
    r"(?:^|\s)pub(?:\s*\([^)]*\))?\s+(?:async\s+|unsafe\s+|const\s+|extern\s+(?:\"[^\"]*\"\s+)?)?(?:fn|struct|enum|trait|type)\b"
)
_JAVA_KT_PRIVATE_RE = re.compile(r"(?:^|\s)(?:private|internal)\s+")
_TS_PRIVATE_METHOD_RE = re.compile(r"^(?:private\s+|protected\s+|#)")


def _strip_leading_decorators_from_signature(sig: str) -> str:
    """Strip leading @decorator(...) / #[attribute] / C# [Attribute] prefixes from a signature string before visibility analysis."""
    if not sig or not sig.startswith(("@", "#[", "#![", "[")):
        return sig
    s = sig
    while s:
        if s.startswith(("#[", "#![", "[")):
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
    decl_kinds = (
        "function_declaration",
        "function_signature",
        "generator_function_declaration",
        "class_declaration",
        "abstract_class_declaration",
        "interface_declaration",
        "type_alias_declaration",
        "enum_declaration",
    )
    for exp in ts_root.find_all(kind="export_statement"):
        for ch in exp.children():
            ck = ch.kind()
            if ck in decl_kinds:
                n = AstGrepIndexer._node_name(ch, lang)
                if n:
                    exports.add(n)
            elif ck == "ambient_declaration":
                for amb_ch in ch.children():
                    ack = amb_ch.kind()
                    if ack in decl_kinds:
                        n = AstGrepIndexer._node_name(amb_ch, lang)
                        if n:
                            exports.add(n)
                    elif ack in ("lexical_declaration", "variable_declaration"):
                        for vd in amb_ch.find_all(kind="variable_declarator"):
                            n = AstGrepIndexer._node_name(vd, lang)
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
    cpp_member_is_private: bool = False,
    parent_is_interface_or_trait: bool = False,
    rust_is_trait_impl: bool = False,
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
        if parent_is_interface_or_trait or rust_is_trait_impl:
            return True
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
        if sym.kind == "method" or sym.parent_symbol:
            if cpp_member_is_private:
                return False
            if sym.parent_symbol and class_is_public is not None and not class_is_public.get(sym.parent_symbol, True):
                return False
            return not bool(re.search(r"\b(?:private|protected)\b", sig_prefix))
        return not bool(re.search(r"\bstatic\b", sig_prefix))

    if suffix == ".cs":
        if sym.name.startswith("_"):
            return False
        if sym.parent_symbol and class_is_public is not None and not class_is_public.get(sym.parent_symbol, False):
            return False
        sig_prefix = re.split(r"[([={]|=>", sig, maxsplit=1)[0] if sig else ""
        if re.search(r"\b(?:private|internal|protected)\b", sig_prefix):
            return False
        if parent_is_interface_or_trait:
            return bool(
                sym.parent_symbol is None
                or class_is_public is None
                or class_is_public.get(sym.parent_symbol, False)
            )
        return bool(re.search(r"\bpublic\b", sig_prefix))

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


def _is_component_surface_symbol(
    sym: CodeSymbol,
    *,
    interface_names: set[str] | frozenset[str] = frozenset(),
    hidden_classes: set[str] | frozenset[str] = frozenset(),
) -> bool:
    """Return True if a symbol belongs on a C4 CMP-* surface (public plus internal/package-scoped; rejects private/protected/_)."""
    if sym.parent_symbol:
        p_leaf = sym.parent_symbol.split("<", 1)[0].split("[", 1)[0].split("::")[-1].split(".")[-1].strip()
        if sym.parent_symbol in hidden_classes or (p_leaf and p_leaf in hidden_classes):
            return False
    if _is_public_symbol(sym):
        return True
    if sym.parent_symbol and sym.parent_symbol.startswith(("_", "#")):
        return False
    if sym.name.startswith(("_", "#")):
        return sym.kind == "method" and sym.name in ("__init__", "__call__")

    suffix = Path(sym.file_path).suffix.lower()
    raw_sig = (sym.signature or "").strip()
    sig = _strip_leading_decorators_from_signature(raw_sig)
    sig_prefix = re.split(r"[([={]|=>", sig, maxsplit=1)[0] if sig else ""

    if suffix == ".cs":
        if re.search(r"\bprivate\b", sig_prefix):
            return False
        if re.search(r"\bprotected\b", sig_prefix) and not re.search(
            r"\b(?:protected\s+internal|internal\s+protected)\b", sig_prefix
        ):
            return False
        if sym.parent_symbol:
            if sym.parent_symbol in interface_names:
                return True
            return bool(re.search(r"\b(?:public|internal)\b", sig_prefix))
        return True

    if suffix in (".java", ".kt", ".kts"):
        return not bool(re.search(r"\b(?:private|protected)\b", sig_prefix))

    if suffix in (".ts", ".tsx", ".js", ".jsx", ".vue", ".svelte"):
        return not bool(_TS_PRIVATE_METHOD_RE.match(sig))

    if suffix in (".c", ".cpp", ".h", ".hpp"):
        if sym.parent_symbol or sym.kind == "method":
            return _is_public_symbol(sym)
        return True

    if suffix in (".rb", ".php", ".swift"):
        return not bool(re.search(r"\b(?:private|protected|fileprivate)\b", sig_prefix))

    # Python, Go, Rust: allow non-underscore package/module-scoped symbols within a CMP-* component
    return True


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


def _extract_overload_param_suffix(sig: str, name: str) -> str:
    """Extract normalized ``(...)`` parameter suffix (plus generic ``<...>`` if present) for overload disambiguation."""
    stripped = _strip_leading_decorators_from_signature((sig or "").strip())
    if not stripped or "(" not in stripped:
        return ""
    search_start = 0
    if name:
        m_name = re.search(rf"(?<![A-Za-z0-9_$]){re.escape(name)}(?![A-Za-z0-9_$])", stripped)
        if m_name:
            search_start = m_name.end()
    tail = stripped[search_start:].lstrip()
    type_params = ""
    if tail.startswith("<"):
        angle_depth = 0
        for idx, ch in enumerate(tail):
            if ch == "<":
                angle_depth += 1
            elif ch == ">":
                angle_depth -= 1
                if angle_depth == 0:
                    type_params = re.sub(r"\s+", " ", tail[: idx + 1].strip())
                    tail = tail[idx + 1 :].lstrip()
                    break
    open_idx = tail.find("(")
    if open_idx == -1:
        open_idx = stripped.find("(")
        if open_idx == -1:
            return type_params
        tail = stripped[open_idx:]
    else:
        tail = tail[open_idx:]
    depth = 0
    in_q: str | None = None
    esc = False
    for idx, ch in enumerate(tail):
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
            if ch == "'" and _RUST_LIFETIME_AT_START_RE.match(tail[idx:]):
                continue
            in_q = ch
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                raw_params = re.sub(r"\s+", " ", tail[: idx + 1].strip())
                raw_params = re.sub(r"\(\s+", "(", raw_params)
                raw_params = re.sub(r"\s+\)", ")", raw_params)
                raw_params = re.sub(r"\s*,\s*", ", ", raw_params)
                return f"{type_params}{raw_params}"
    return type_params


def _build_overload_safe_symbol_map(
    filtered_syms: list[CodeSymbol],
    *,
    collapse_same_params: bool = False,
) -> dict[tuple[str, str, str], str]:
    """Build ``(kind, parent, name) -> signature`` without clobbering overloaded functions or methods."""
    groups: dict[tuple[str, str, str], list[CodeSymbol]] = {}
    for s in filtered_syms:
        groups.setdefault((s.kind, s.parent_symbol or "", s.name), []).append(s)

    out: dict[tuple[str, str, str], str] = {}
    for base_key, group in groups.items():
        if len(group) == 1:
            out[base_key] = group[0].signature or ""
            continue
        kind, parent, name = base_key
        entries: list[tuple[str, str]] = []
        if collapse_same_params:
            by_param: dict[str, str] = {}
            for s in group:
                sig = s.signature or ""
                psuffix = _extract_overload_param_suffix(sig, name)
                if psuffix not in by_param or len(sig) > len(by_param[psuffix]):
                    by_param[psuffix] = sig
            entries = list(by_param.items())
        else:
            for s in group:
                sig = s.signature or ""
                entries.append((_extract_overload_param_suffix(sig, name), sig))

        if len(entries) == 1:
            out[base_key] = entries[0][1]
            continue

        for idx, (psuffix, sig) in enumerate(entries):
            if idx == 0:
                out[base_key] = sig
            else:
                disambig = f"{name}{psuffix}" if psuffix else f"{name}#{idx + 1}"
                cand_key = (kind, parent, disambig)
                n = 2
                while cand_key in out:
                    cand_key = (kind, parent, f"{disambig}#{n}")
                    n += 1
                out[cand_key] = sig
    return out


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
    return _build_overload_safe_symbol_map(
        [s for s in syms if _is_public_symbol(s)],
        collapse_same_params=suffix in (".c", ".cpp", ".h", ".hpp"),
    )


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
    out = _build_overload_safe_symbol_map(
        [s for s in syms if _is_boundary_export_symbol(s, py_dunder_all=py_dunder_all)],
        collapse_same_params=suffix in (".c", ".cpp", ".h", ".hpp"),
    )
    for k, v in reexported.items():
        if not any(existing_k[2].split("(", 1)[0] == k[2] for existing_k in out):
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
    ts_lang = (
        "tsx" if suffix in (".tsx", ".jsx", ".vue", ".svelte") else ("typescript" if suffix == ".ts" else "javascript")
    )
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


_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
_NEXT_APP_ROUTE_RE = re.compile(r"(?:^|/)(?:src/)?app/(.+)/route\.(?:ts|tsx|js|jsx|mjs)$")


def _blank_comments_outside_strings(content: str, *, hash_comments: bool) -> str:
    """Blank line/block comments outside quoted strings while preserving exact 1-based line offsets."""
    out: list[str] = []
    n = len(content)
    i = 0
    in_q: str | None = None
    triple_q: str | None = None
    while i < n:
        ch = content[i]
        if triple_q is not None:
            if content.startswith(triple_q, i):
                out.append(triple_q)
                i += 3
                triple_q = None
            else:
                out.append(ch)
                i += 1
            continue
        if in_q is not None:
            if ch == "\\" and i + 1 < n:
                out.append(content[i : i + 2])
                i += 2
                continue
            if ch == in_q:
                in_q = None
            out.append(ch)
            i += 1
            continue
        if hash_comments and (content.startswith('"""', i) or content.startswith("'''", i)):
            triple_q = content[i : i + 3]
            out.append(triple_q)
            i += 3
            continue
        if ch in ("'", '"', "`"):
            in_q = ch
            out.append(ch)
            i += 1
            continue
        if hash_comments and ch == "#":
            while i < n and content[i] not in ("\n", "\r"):
                out.append(" ")
                i += 1
            continue
        if not hash_comments and ch == "/" and i + 1 < n:
            nxt = content[i + 1]
            if nxt == "/":
                while i < n and content[i] not in ("\n", "\r"):
                    out.append(" ")
                    i += 1
                continue
            if nxt == "*":
                out.append("  ")
                i += 2
                while i < n and not content.startswith("*/", i):
                    out.append(content[i] if content[i] in ("\n", "\r") else " ")
                    i += 1
                if i < n:
                    out.append("  ")
                    i += 2
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def normalize_http_route_path(raw_path: str) -> str:
    """Normalize dynamic framework/client route segments (:id, {id}, <int:id>, [id], ${expr}) to '{*}'."""
    s = (raw_path or "").strip().strip("\"'`").strip()
    if not s:
        return ""
    if s.startswith(("http://", "https://")):
        s = re.sub(r"^https?://[^/?#]+", "", s) or "/"
    s = re.split(r"[?#]", s, maxsplit=1)[0].strip()
    if not s:
        return ""
    s = re.sub(r"\$\{[^}]+\}", "{*}", s)
    s = re.sub(r"<[^>/]+>", "{*}", s)
    s = re.sub(r"\[\[\.\.\.[^\]]+\]\]|\[\.\.\.[^\]]+\]|\[[^\]]+\]", "{*}", s)
    s = re.sub(r"\{[^}/]+\}", "{*}", s)
    if not s.startswith("/"):
        return ""
    segs: list[str] = []
    for raw_seg in s.split("/"):
        seg = raw_seg.strip()
        if not seg:
            continue
        if seg.startswith((":", "*")) or seg == "{*}" or "{*}" in seg:
            segs.append("{*}")
        else:
            segs.append(seg)
    return "/" + "/".join(segs) if segs else "/"


def _join_http_paths(prefix: str, subpath: str) -> str:
    p_clean = (prefix or "").strip().strip("\"'`").strip()
    s_clean = (subpath or "").strip().strip("\"'`").strip()
    if s_clean.startswith(("http://", "https://")):
        return normalize_http_route_path(s_clean)
    if p_clean.startswith(("http://", "https://")):
        p_clean = re.sub(r"^https?://[^/?#]+", "", p_clean) or "/"
    if p_clean and not p_clean.startswith("/"):
        p_clean = "/" + p_clean
    p_clean = p_clean.rstrip("/")
    if not s_clean or s_clean == "/":
        combined = p_clean if p_clean else "/"
        return normalize_http_route_path(combined)
    if not s_clean.startswith("/"):
        s_clean = "/" + s_clean
    return normalize_http_route_path(f"{p_clean}{s_clean}")


def _unquote_str_token(tok: str) -> tuple[str | None, bool]:
    """Return (inner_text, is_template_or_fstring) if tok is a string literal, else (None, False)."""
    t = tok.strip()
    is_tmpl = False
    if t.startswith(("f'", 'f"', "F'", 'F"')):
        is_tmpl = True
        t = t[1:]
    elif t.startswith("`") and t.endswith("`") and len(t) >= 2:
        return t[1:-1], True
    if len(t) >= 2 and t[0] in ("'", '"') and t[-1] == t[0]:
        return t[1:-1], is_tmpl
    return None, False


def _expand_template_text(text: str, consts: dict[str, str]) -> str:
    """Substitute known ${CONST} / {CONST} identifiers and normalize remaining interpolations to {*}."""

    def _sub_js(m: re.Match[str]) -> str:
        inner = m.group(1).strip()
        return consts.get(inner, "{*}")

    def _sub_py(m: re.Match[str]) -> str:
        inner = m.group(1).strip().split("!", 1)[0].split(":", 1)[0].strip()
        if inner == "*":
            return "{*}"
        return consts.get(inner, "{*}")

    res = re.sub(r"\$\{([^}]+)\}", _sub_js, text)
    return re.sub(r"\{([^}/]+)\}", _sub_py, res)


def _eval_url_expr(
    expr: str,
    consts: dict[str, str],
    helpers: dict[str, tuple[str, str]],
    base_prefix: str = "",
) -> str:
    """ deterministically evaluate a literal, template/f-string, concatenation, constant, or prefix helper call."""
    raw = (expr or "").strip()
    if not raw:
        return ""
    m_call = re.match(r"^([A-Za-z_$][A-Za-z0-9_$]*)\s*\((.+)\)$", raw, re.DOTALL)
    if m_call and m_call.group(1) in helpers:
        param_name, tmpl = helpers[m_call.group(1)]
        arg_val = _eval_url_expr(m_call.group(2).split(",", 1)[0], consts, {}, "")
        if not arg_val:
            return ""
        local_consts = {**consts, param_name: arg_val}
        resolved = _expand_template_text(tmpl, local_consts)
        return _join_http_paths(base_prefix, resolved) if base_prefix else normalize_http_route_path(resolved)

    if "+" in raw and not (raw.startswith(("`", "f'", 'f"')) and raw.endswith(("`", "'", '"'))):
        parts = [p.strip() for p in re.split(r"\+", raw) if p.strip()]
        if len(parts) >= 2:
            acc: list[str] = []
            has_static = False
            for part in parts:
                lit, is_t = _unquote_str_token(part)
                if lit is not None:
                    expanded = _expand_template_text(lit, consts) if is_t else lit
                    if "/" in expanded:
                        has_static = True
                    acc.append(expanded)
                elif part in consts:
                    has_static = True
                    acc.append(consts[part])
                else:
                    acc.append("{*}")
            if has_static:
                joined = "".join(acc)
                return _join_http_paths(base_prefix, joined) if base_prefix else normalize_http_route_path(joined)

    lit, is_t = _unquote_str_token(raw)
    if lit is not None:
        expanded = _expand_template_text(lit, consts) if is_t else lit
        if not expanded:
            return normalize_http_route_path(base_prefix) if base_prefix else ""
        if base_prefix and not expanded.startswith(("http://", "https://")):
            return _join_http_paths(base_prefix, expanded)
        if not expanded.startswith(("/", "http://", "https://")):
            return ""
        if expanded.strip("/") == "{*}" and "/" not in lit.replace("${", "").replace("{", ""):
            return ""
        return normalize_http_route_path(expanded)

    if raw in consts:
        cval = consts[raw]
        return _join_http_paths(base_prefix, cval) if base_prefix else normalize_http_route_path(cval)
    return ""


def _collect_intra_file_url_env(
    cleaned: str, suffix: str
) -> tuple[dict[str, str], dict[str, tuple[str, str]], dict[str, str]]:
    """Collect intra-file string URL constants, 1-arg prefix helpers, and HTTP client instances."""
    consts: dict[str, str] = {}
    helpers: dict[str, tuple[str, str]] = {}
    clients: dict[str, str] = {}

    assign_re = (
        re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?::[^=\n]+)?=\s*(f?\"[^\n\"]*\"|f?'[^\n']*')", re.MULTILINE)
        if suffix == ".py"
        else re.compile(
            r"\b(?:const|let|var|val|final|static|readonly)\s+(?:[A-Za-z_][A-Za-z0-9_<>]*\s+)?([A-Za-z_$][A-Za-z0-9_$]*)\s*(?::[^=\n]+)?=\s*(`[^`]*`|\"[^\n\"]*\"|'[^\n']*')"
        )
    )
    raw_assigns = [(m.group(1), m.group(2)) for m in assign_re.finditer(cleaned)]
    for _ in range(2):
        for name, rhs in raw_assigns:
            lit, is_t = _unquote_str_token(rhs)
            if lit is not None:
                val = _expand_template_text(lit, consts) if is_t else lit
                if val.startswith(("/", "http://", "https://")):
                    consts[name] = val.rstrip("/") if len(val) > 1 else val

    if suffix == ".py":
        for m in re.finditer(
            r"def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)(?:\s*:[^)]*)?\)\s*(?:->[^:]+)?:\s*\n?\s*return\s+(f?\"[^\n\"]+\"|f?'[^\n']+')",
            cleaned,
        ):
            lit, _ = _unquote_str_token(m.group(3))
            if lit:
                helpers[m.group(1)] = (m.group(2), lit)
        for m in re.finditer(
            r"(?:with\s+|async\s+with\s+)?(?:httpx\.(?:Async)?Client|requests\.Session)\s*\(([^)]*)\)\s*(?:as\s+([A-Za-z_][A-Za-z0-9_]*))?",
            cleaned,
        ):
            args_txt, as_var = m.group(1), m.group(2)
            lhs_m = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*$", cleaned[: m.start()])
            cvar = as_var or (lhs_m.group(1) if lhs_m else "")
            if cvar:
                b_m = re.search(r"base_url\s*=\s*([^,\s)]+)", args_txt)
                base_v = _eval_url_expr(b_m.group(1), consts, {}, "") if b_m else ""
                clients[cvar] = base_v
    else:
        for m in re.finditer(
            r"(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*\(\s*([A-Za-z_$][A-Za-z0-9_$]*)(?:\s*:[^)]*)?\)\s*(?::[^=]+)?=>\s*(`[^`]+`)",
            cleaned,
        ):
            lit, _ = _unquote_str_token(m.group(3))
            if lit:
                helpers[m.group(1)] = (m.group(2), lit)
        for m in re.finditer(
            r"function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(\s*([A-Za-z_$][A-Za-z0-9_$]*)(?:\s*:[^)]*)?\)\s*(?::[^{]+)?\{\s*return\s+(`[^`]+`)",
            cleaned,
        ):
            lit, _ = _unquote_str_token(m.group(3))
            if lit:
                helpers[m.group(1)] = (m.group(2), lit)
        for m in re.finditer(
            r"(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(axios|ky)\.create\s*\(\s*\{([^}]*)\}",
            cleaned,
        ):
            cvar, lib, body_txt = m.group(1), m.group(2), m.group(3)
            key = "baseURL" if lib == "axios" else "prefixUrl"
            b_m = re.search(rf"{key}\s*:\s*([^,\n}}]+)", body_txt)
            clients[cvar] = _eval_url_expr(b_m.group(1), consts, helpers, "") if b_m else ""
    return consts, helpers, clients


def _enclosing_parent_at_line(symbols: list[CodeSymbol], line_no: int) -> str | None:
    best: CodeSymbol | None = None
    for s in symbols:
        if (
            s.kind in ("class", "struct")
            and s.line_start <= line_no <= s.line_end
            and (best is None or (s.line_end - s.line_start) <= (best.line_end - best.line_start))
        ):
            best = s
    return best.name if best is not None else None


def _split_top_level_args(arg_str: str) -> list[str]:
    parts: list[str] = []
    cur: list[str] = []
    depth = 0
    in_q: str | None = None
    esc = False
    for ch in arg_str:
        if esc:
            cur.append(ch)
            esc = False
            continue
        if ch == "\\":
            cur.append(ch)
            esc = True
            continue
        if in_q is not None:
            cur.append(ch)
            if ch == in_q:
                in_q = None
            continue
        if ch in ("'", '"', "`"):
            in_q = ch
            cur.append(ch)
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}" and depth > 0:
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    tail = "".join(cur).strip()
    if tail:
        parts.append(tail)
    return parts


def _extract_balanced_call_args(text: str, open_paren_idx: int) -> tuple[str, int]:
    """Return (inside_parens_text, close_paren_idx) starting at '(' index, or ('', -1)."""
    if open_paren_idx < 0 or open_paren_idx >= len(text) or text[open_paren_idx] != "(":
        return "", -1
    depth = 0
    in_q: str | None = None
    esc = False
    for idx in range(open_paren_idx, len(text)):
        ch = text[idx]
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
                return text[open_paren_idx + 1 : idx], idx
    return "", -1


_TS_HTTP_ENDPOINT_HINT_RE = re.compile(
    r"@(?:Controller|Get|Post|Put|Patch|Delete|Options|Head)\b|\.(?:get|post|put|patch|delete|options|head)\s*\("
)
_TS_HTTP_REQUEST_HINT_RE = re.compile(r"\b(?:fetch|EventSource|axios|ky|http|httpClient)\b")


def _extract_polyglot_http_endpoints(
    indexer: AstGrepIndexer,
    p: Path,
    content: str,
    *,
    _symbols: list[CodeSymbol] | None = None,
) -> list[CodeSymbol]:
    suffix = p.suffix.lower()
    rel_posix = p.as_posix()
    supported = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".go", ".rs", ".java", ".kt", ".kts", ".cs"}
    if suffix not in supported or not content:
        return []

    is_next_route = bool(_NEXT_APP_ROUTE_RE.search(rel_posix))
    if not is_next_route:
        if suffix == ".py" and "@" not in content and "Route(" not in content:
            return []
        if suffix in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts") and not _TS_HTTP_ENDPOINT_HINT_RE.search(content):
            return []
        if suffix == ".go" and "HandleFunc" not in content and ".GET(" not in content and ".POST(" not in content and ".PUT(" not in content and ".PATCH(" not in content and ".DELETE(" not in content and ".Get(" not in content and ".Post(" not in content and ".Put(" not in content and ".Patch(" not in content and ".Delete(" not in content:
            return []
        if suffix == ".rs" and ".route(" not in content and "#[" not in content:
            return []
        if suffix in (".java", ".kt", ".kts") and "Mapping" not in content:
            return []
        if suffix == ".cs" and "Http" not in content and ".Map" not in content:
            return []

    cleaned = _blank_comments_outside_strings(content, hash_comments=(suffix == ".py"))
    consts, helpers, clients = _collect_intra_file_url_env(cleaned, suffix)
    struct_syms = _symbols if _symbols is not None else indexer.extract_symbols(p, content)
    results: list[CodeSymbol] = []
    seen: set[tuple[str, int]] = set()

    def _emit(method: str, norm_path: str, raw_sig: str, offset: int, end_offset: int | None = None) -> None:
        verb = method.upper().strip()
        if verb not in _HTTP_METHODS or not norm_path:
            return
        line_start = cleaned.count("\n", 0, offset) + 1
        line_end = cleaned.count("\n", 0, end_offset) + 1 if end_offset is not None else line_start
        sym_name = f"{verb} {norm_path}"
        if (sym_name, line_start) in seen:
            return
        seen.add((sym_name, line_start))
        results.append(
            CodeSymbol(
                name=sym_name,
                kind="http_endpoint",
                file_path=str(p),
                line_start=line_start,
                line_end=max(line_start, line_end),
                signature=raw_sig.strip() or norm_path,
                parent_symbol=_enclosing_parent_at_line(struct_syms, line_start),
            )
        )

    # 1. Next.js App Router: app/**/route.{ts,tsx,js,jsx,mjs}
    m_next = _NEXT_APP_ROUTE_RE.search(rel_posix)
    if m_next:
        raw_segs = [
            seg
            for seg in m_next.group(1).split("/")
            if seg and not (seg.startswith("(") and seg.endswith(")")) and not seg.startswith("@")
        ]
        next_path = normalize_http_route_path("/" + "/".join(raw_segs))
        for m_exp in re.finditer(
            r"\bexport\s+(?:async\s+)?(?:function|const|let)\s+(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b",
            cleaned,
        ):
            _emit(m_exp.group(1), next_path, f"/{'/'.join(raw_segs)}", m_exp.start(), m_exp.end())
        return results

    # 2. Python: FastAPI / APIRouter, Flask / Blueprint, Starlette Route
    if suffix == ".py":
        router_prefixes: dict[str, str] = {}
        for m_r in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:APIRouter|Blueprint)\s*\(", cleaned):
            args_txt, _ = _extract_balanced_call_args(cleaned, m_r.end() - 1)
            m_pref = re.search(r"(?:url_)?prefix\s*=\s*(\"[^\"]+\"|'[^']+')", args_txt)
            router_prefixes[m_r.group(1)] = m_pref.group(1).strip("\"'") if m_pref else ""
        for m_inc in re.finditer(r"\.(?:include_router|register_blueprint)\s*\(", cleaned):
            args_txt, _ = _extract_balanced_call_args(cleaned, m_inc.end() - 1)
            args = _split_top_level_args(args_txt)
            if args:
                rname = args[0].strip()
                m_pref = re.search(r"(?:url_)?prefix\s*=\s*(\"[^\"]+\"|'[^']+')", args_txt)
                if m_pref and rname in router_prefixes:
                    inc_p = m_pref.group(1).strip("\"'").rstrip("/")
                    sub_p = router_prefixes[rname].lstrip("/")
                    router_prefixes[rname] = f"{inc_p}/{sub_p}".rstrip("/") if sub_p else inc_p

        for m_dec in re.finditer(
            r"@([A-Za-z_][A-Za-z0-9_]*)\.(get|post|put|patch|delete|options|head|route)\s*\(",
            cleaned,
        ):
            obj, verb_or_route = m_dec.group(1), m_dec.group(2).lower()
            args_txt, close_idx = _extract_balanced_call_args(cleaned, m_dec.end() - 1)
            args = _split_top_level_args(args_txt)
            if not args:
                continue
            raw_p, _ = _unquote_str_token(args[0])
            if raw_p is None:
                raw_p = consts.get(args[0].strip())
            if raw_p is None:
                continue
            full_path = _join_http_paths(router_prefixes.get(obj, ""), raw_p)
            if verb_or_route == "route":
                m_methods = re.search(r"methods\s*=\s*\[([^\]]+)\]", args_txt)
                verbs = re.findall(r"[\"']([A-Za-z]+)[\"']", m_methods.group(1)) if m_methods else ["GET"]
                for v in verbs:
                    _emit(v, full_path, raw_p, m_dec.start(), close_idx)
            else:
                _emit(verb_or_route, full_path, raw_p, m_dec.start(), close_idx)

        for m_st in re.finditer(r"\bRoute\s*\(", cleaned):
            args_txt, close_idx = _extract_balanced_call_args(cleaned, m_st.end() - 1)
            args = _split_top_level_args(args_txt)
            if len(args) >= 2:
                raw_p, _ = _unquote_str_token(args[0])
                if raw_p is not None:
                    m_methods = re.search(r"methods\s*=\s*\[([^\]]+)\]", args_txt)
                    verbs = re.findall(r"[\"']([A-Za-z]+)[\"']", m_methods.group(1)) if m_methods else ["GET"]
                    norm_p = normalize_http_route_path(raw_p)
                    for v in verbs:
                        _emit(v, norm_p, raw_p, m_st.start(), close_idx)
        return results

    # 3. TS/JS: NestJS Controllers + Express / Fastify / Hono / Koa
    if suffix in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts"):
        class_prefixes: dict[str, str] = {}
        for cls_sym in [s for s in struct_syms if s.kind == "class"]:
            cls_start_idx = sum(len(ln) + 1 for ln in cleaned.splitlines()[: max(0, cls_sym.line_start - 1)])
            window = cleaned[max(0, cls_start_idx - 240) : cls_start_idx + 180]
            m_ctrl = re.search(r"@Controller\s*\(\s*([^)]*)\)", window)
            if m_ctrl is not None:
                raw_arg = m_ctrl.group(1).strip()
                lit, _ = _unquote_str_token(raw_arg) if raw_arg else ("", False)
                class_prefixes[cls_sym.name] = lit or ""

        for m_nest in re.finditer(r"@(Get|Post|Put|Patch|Delete|Options|Head)\s*\(\s*([^)]*)\)", cleaned):
            line_no = cleaned.count("\n", 0, m_nest.start()) + 1
            parent_cls = _enclosing_parent_at_line(struct_syms, line_no)
            if parent_cls and parent_cls in class_prefixes:
                raw_sub = m_nest.group(2).strip()
                sub_lit, _ = _unquote_str_token(raw_sub) if raw_sub else ("", False)
                sub_path = sub_lit or ""
                full_p = _join_http_paths(class_prefixes[parent_cls], sub_path)
                _emit(m_nest.group(1), full_p, sub_path or "/", m_nest.start(), m_nest.end())

        router_mounts: dict[str, str] = {}
        for m_hono in re.finditer(
            r"(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*new\s+Hono[^\n;]*?\.basePath\s*\(\s*(\"[^\"]+\"|'[^']+')\s*\)",
            cleaned,
        ):
            router_mounts[m_hono.group(1)] = m_hono.group(2).strip("\"'")
        for m_use in re.finditer(
            r"\b[A-Za-z_$][A-Za-z0-9_$]*\.(?:use|route)\s*\(\s*(\"[^\"]+\"|'[^']+')\s*,\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*\)",
            cleaned,
        ):
            router_mounts[m_use.group(2)] = m_use.group(1).strip("\"'")

        for m_ex in re.finditer(
            r"(?<![.\w$])([A-Za-z_$][A-Za-z0-9_$]*)\.(get|post|put|patch|delete|options|head)\s*\(",
            cleaned,
        ):
            obj, verb = m_ex.group(1), m_ex.group(2)
            if obj in clients or obj in (
                "axios",
                "ky",
                "http",
                "httpClient",
                "Reflect",
                "Object",
                "Map",
                "Set",
                "Headers",
                "URLSearchParams",
                "formData",
                "params",
                "searchParams",
                "cookies",
                "headers",
            ):
                continue
            args_txt, close_idx = _extract_balanced_call_args(cleaned, m_ex.end() - 1)
            args = _split_top_level_args(args_txt)
            if len(args) < 2 or args[-1].lstrip().startswith("{"):
                continue
            raw_p = _eval_url_expr(args[0], consts, helpers, router_mounts.get(obj, ""))
            if raw_p:
                lit, _ = _unquote_str_token(args[0])
                _emit(verb, raw_p, lit or args[0].strip(), m_ex.start(), close_idx)
        return results

    # 4. Go: net/http HandleFunc, Gin, Echo, Chi
    if suffix == ".go":
        go_groups: dict[str, str] = {}
        for m_grp in re.finditer(
            r"\b([A-Za-z_][A-Za-z0-9_]*)\s*:?=\s*([A-Za-z_][A-Za-z0-9_]*)\.Group\s*\(\s*\"([^\"]+)\"",
            cleaned,
        ):
            parent_p = go_groups.get(m_grp.group(2), "")
            go_groups[m_grp.group(1)] = _join_http_paths(parent_p, m_grp.group(3))
        for m_hf in re.finditer(r"\b(?:[A-Za-z_][A-Za-z0-9_]*\.)?HandleFunc\s*\(\s*\"([^\"]+)\"", cleaned):
            pat = m_hf.group(1).strip()
            m_verb = re.match(r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(.+)$", pat)
            verb, route_p = (m_verb.group(1), m_verb.group(2)) if m_verb else ("GET", pat)
            _emit(verb, normalize_http_route_path(route_p), pat, m_hf.start(), m_hf.end())
        for m_ge in re.finditer(
            r"\b([A-Za-z_][A-Za-z0-9_]*)\.(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|Get|Post|Put|Patch|Delete|Head|Options)\s*\(\s*\"([^\"]+)\"",
            cleaned,
        ):
            obj, verb, raw_p = m_ge.group(1), m_ge.group(2), m_ge.group(3)
            _emit(verb, _join_http_paths(go_groups.get(obj, ""), raw_p), raw_p, m_ge.start(), m_ge.end())
        return results

    # 5. Rust: axum Router::route & actix-web attributes
    if suffix == ".rs":
        for m_act in re.finditer(
            r"#\[\s*(?:actix_web\s*::\s*)?(get|post|put|patch|delete|head|options)\s*\(\s*\"([^\"]+)\"",
            cleaned,
        ):
            _emit(m_act.group(1), normalize_http_route_path(m_act.group(2)), m_act.group(2), m_act.start(), m_act.end())
        for m_rt in re.finditer(r"\.route\s*\(", cleaned):
            args_txt, close_idx = _extract_balanced_call_args(cleaned, m_rt.end() - 1)
            args = _split_top_level_args(args_txt)
            if len(args) >= 2:
                raw_p, _ = _unquote_str_token(args[0])
                if raw_p:
                    verbs = re.findall(
                        r"\b(?:axum::routing::|routing::)?(get|post|put|patch|delete|head|options)\s*\(",
                        args[1],
                    )
                    for v in verbs or ["GET"]:
                        _emit(v, normalize_http_route_path(raw_p), raw_p, m_rt.start(), close_idx)
        return results

    # 6. Java / Kotlin: Spring @RequestMapping + @GetMapping/@PostMapping/...
    if suffix in (".java", ".kt", ".kts"):
        cls_prefixes: dict[str, str] = {}
        for cls_sym in [s for s in struct_syms if s.kind == "class"]:
            cls_idx = sum(len(ln) + 1 for ln in cleaned.splitlines()[: max(0, cls_sym.line_start - 1)])
            win = cleaned[max(0, cls_idx - 260) : cls_idx + 200]
            m_rm = re.search(r"@RequestMapping\s*\(([^)]*)\)", win)
            if m_rm:
                m_val = re.search(r"(?:(?:value|path)\s*=\s*)?\"([^\"]+)\"", m_rm.group(1))
                cls_prefixes[cls_sym.name] = m_val.group(1) if m_val else ""

        for m_sp in re.finditer(r"@(Get|Post|Put|Patch|Delete)Mapping(?:\s*\(([^)]*)\))?", cleaned):
            line_no = cleaned.count("\n", 0, m_sp.start()) + 1
            parent_cls = _enclosing_parent_at_line(struct_syms, line_no)
            args_s = m_sp.group(2) or ""
            m_val = re.search(r"(?:(?:value|path)\s*=\s*)?\"([^\"]*)\"", args_s)
            sub_p = m_val.group(1) if m_val else ""
            full_p = _join_http_paths(cls_prefixes.get(parent_cls or "", ""), sub_p)
            _emit(m_sp.group(1), full_p, sub_p or "/", m_sp.start(), m_sp.end())

        for m_rm in re.finditer(r"@RequestMapping\s*\(([^)]*)\)", cleaned):
            args_s = m_rm.group(1)
            m_meth = re.search(r"RequestMethod\.(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)", args_s)
            if not m_meth:
                continue
            line_no = cleaned.count("\n", 0, m_rm.start()) + 1
            parent_cls = _enclosing_parent_at_line(struct_syms, line_no)
            m_val = re.search(r"(?:(?:value|path)\s*=\s*)?\"([^\"]*)\"", args_s)
            sub_p = m_val.group(1) if m_val else ""
            full_p = _join_http_paths(cls_prefixes.get(parent_cls or "", ""), sub_p)
            _emit(m_meth.group(1), full_p, sub_p or "/", m_rm.start(), m_rm.end())
        return results

    # 7. C# ASP.NET Core: [Route] + [HttpGet]/[HttpPost] + Minimal APIs MapGet/MapPost
    cs_prefixes: dict[str, str] = {}
    for cls_sym in [s for s in struct_syms if s.kind == "class"]:
        cls_idx = sum(len(ln) + 1 for ln in cleaned.splitlines()[: max(0, cls_sym.line_start - 1)])
        win = cleaned[max(0, cls_idx - 260) : cls_idx + 200]
        m_rt = re.search(r"\[\s*Route\s*\(\s*\"([^\"]+)\"\s*\)\s*\]", win)
        if m_rt:
            ctrl_slug = re.sub(r"Controller$", "", cls_sym.name, flags=re.IGNORECASE).lower()
            cs_prefixes[cls_sym.name] = re.sub(r"\[\s*controller\s*\]", ctrl_slug, m_rt.group(1), flags=re.IGNORECASE)

    for m_cs in re.finditer(
        r"\[\s*Http(Get|Post|Put|Patch|Delete|Head|Options)(?:\s*\(\s*\"([^\"]*)\"\s*\))?\s*\]",
        cleaned,
    ):
        line_no = cleaned.count("\n", 0, m_cs.start()) + 1
        parent_cls = _enclosing_parent_at_line(struct_syms, line_no)
        sub_p = m_cs.group(2) or ""
        if not sub_p:
            near = cleaned[m_cs.end() : m_cs.end() + 140]
            m_sub_rt = re.match(r"\s*\[\s*Route\s*\(\s*\"([^\"]+)\"\s*\)\s*\]", near)
            if m_sub_rt:
                sub_p = m_sub_rt.group(1)
        full_p = _join_http_paths(cs_prefixes.get(parent_cls or "", ""), sub_p)
        if full_p:
            _emit(m_cs.group(1), full_p, sub_p or "/", m_cs.start(), m_cs.end())

    map_groups: dict[str, str] = {}
    for m_mg in re.finditer(
        r"\b([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\.MapGroup\s*\(\s*\"([^\"]+)\"",
        cleaned,
    ):
        map_groups[m_mg.group(1)] = _join_http_paths(map_groups.get(m_mg.group(2), ""), m_mg.group(3))
    for m_min in re.finditer(
        r"\b([A-Za-z_][A-Za-z0-9_]*)\.Map(Get|Post|Put|Patch|Delete|Head|Options)\s*\(\s*\"([^\"]+)\"",
        cleaned,
    ):
        obj, verb, raw_p = m_min.group(1), m_min.group(2), m_min.group(3)
        _emit(verb, _join_http_paths(map_groups.get(obj, ""), raw_p), raw_p, m_min.start(), m_min.end())
    return results


def _extract_polyglot_http_requests(
    indexer: AstGrepIndexer,
    p: Path,
    content: str,
    *,
    _symbols: list[CodeSymbol] | None = None,
) -> list[CodeSymbol]:
    suffix = p.suffix.lower()
    ts_exts = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte"}
    if suffix not in ts_exts and suffix != ".py":
        return []
    if not content:
        return []
    if suffix == ".py" and "requests" not in content and "httpx" not in content:
        return []
    if suffix in ts_exts and not _TS_HTTP_REQUEST_HINT_RE.search(content):
        return []

    blocks = _extract_sfc_script_blocks(content) if suffix in (".vue", ".svelte") else [(content, 0)]
    struct_syms = _symbols if _symbols is not None else indexer.extract_symbols(p, content)
    results: list[CodeSymbol] = []
    seen: set[tuple[str, int]] = set()

    for block_text, line_offset in blocks:
        cleaned = _blank_comments_outside_strings(block_text, hash_comments=(suffix == ".py"))
        consts, helpers, clients = _collect_intra_file_url_env(cleaned, suffix)

        def _emit_req(
            method: str,
            norm_path: str,
            raw_sig: str,
            offset: int,
            end_offset: int | None = None,
            _line_offset: int = line_offset,
            _cleaned: str = cleaned,
        ) -> None:
            verb = method.upper().strip()
            if verb not in _HTTP_METHODS or not norm_path:
                return
            line_start = _line_offset + _cleaned.count("\n", 0, offset) + 1
            line_end = _line_offset + _cleaned.count("\n", 0, end_offset) + 1 if end_offset is not None else line_start
            sym_name = f"{verb} {norm_path}"
            if (sym_name, line_start) in seen:
                return
            seen.add((sym_name, line_start))
            results.append(
                CodeSymbol(
                    name=sym_name,
                    kind="http_request",
                    file_path=str(p),
                    line_start=line_start,
                    line_end=max(line_start, line_end),
                    signature=raw_sig.strip() or norm_path,
                    parent_symbol=_enclosing_parent_at_line(struct_syms, line_start),
                )
            )

        if suffix == ".py":
            if not re.search(r"\b(?:requests|httpx)\b", cleaned):
                continue
            client_names = {"requests": "", "httpx": "", **clients}
            for m_py in re.finditer(
                r"\b([A-Za-z_][A-Za-z0-9_]*)\.(get|post|put|patch|delete|head|options|request)\s*\(",
                cleaned,
            ):
                obj, verb_or_req = m_py.group(1), m_py.group(2).lower()
                if obj not in client_names:
                    continue
                args_txt, close_idx = _extract_balanced_call_args(cleaned, m_py.end() - 1)
                args = _split_top_level_args(args_txt)
                if not args:
                    continue
                if verb_or_req == "request":
                    if len(args) < 2:
                        continue
                    v_lit, _ = _unquote_str_token(args[0])
                    if not v_lit:
                        continue
                    verb, url_arg = v_lit, args[1]
                else:
                    verb, url_arg = verb_or_req, args[0]
                norm_p = _eval_url_expr(url_arg, consts, helpers, client_names[obj])
                if norm_p:
                    _emit_req(verb, norm_p, url_arg, m_py.start(), close_idx)
            continue

        # TS / JS / Vue / Svelte
        for m_f in re.finditer(r"(?<![.\w$])(?:(?:window|globalThis)\.)?fetch\s*\(", cleaned):
            args_txt, close_idx = _extract_balanced_call_args(cleaned, m_f.end() - 1)
            args = _split_top_level_args(args_txt)
            if not args:
                continue
            verb = "GET"
            if len(args) >= 2:
                m_meth = re.search(r"\bmethod\s*:\s*[\"']([A-Za-z]+)[\"']", args[1])
                if m_meth:
                    verb = m_meth.group(1)
            norm_p = _eval_url_expr(args[0], consts, helpers, "")
            if norm_p:
                _emit_req(verb, norm_p, args[0], m_f.start(), close_idx)

        for m_es in re.finditer(r"\bnew\s+(?:(?:window|globalThis)\.)?EventSource\s*\(", cleaned):
            args_txt, close_idx = _extract_balanced_call_args(cleaned, m_es.end() - 1)
            args = _split_top_level_args(args_txt)
            if args:
                norm_p = _eval_url_expr(args[0], consts, helpers, "")
                if norm_p:
                    _emit_req("GET", norm_p, args[0], m_es.start(), close_idx)

        for m_ax_cfg in re.finditer(r"(?<![.\w$])axios\s*\(\s*\{", cleaned):
            args_txt, close_idx = _extract_balanced_call_args(cleaned, m_ax_cfg.end() - 2)
            m_url = re.search(r"\burl\s*:\s*([^,\n}]+)", args_txt)
            if m_url:
                m_meth = re.search(r"\bmethod\s*:\s*[\"']([A-Za-z]+)[\"']", args_txt)
                verb = m_meth.group(1) if m_meth else "GET"
                norm_p = _eval_url_expr(m_url.group(1), consts, helpers, "")
                if norm_p:
                    _emit_req(verb, norm_p, m_url.group(1), m_ax_cfg.start(), close_idx)

        ts_clients = {"axios": "", "ky": "", "this.http": "", "this.httpClient": "", "http": "", "httpClient": "", **clients}
        for m_cl in re.finditer(
            r"((?:this\.)?[A-Za-z_$][A-Za-z0-9_$]*)\.(get|post|put|patch|delete|head|options)\s*(?:<[^>()]+>)?\s*\(",
            cleaned,
        ):
            obj, verb = m_cl.group(1), m_cl.group(2)
            if obj not in ts_clients:
                continue
            args_txt, close_idx = _extract_balanced_call_args(cleaned, m_cl.end() - 1)
            args = _split_top_level_args(args_txt)
            if not args:
                continue
            norm_p = _eval_url_expr(args[0], consts, helpers, ts_clients[obj])
            if norm_p:
                _emit_req(verb, norm_p, args[0], m_cl.start(), close_idx)

    return results


def get_ast_indexer() -> BaseAstIndexer:
    """Factory retrieving the unified polyglot AST indexer (AstGrepIndexer)."""
    return AstGrepIndexer()
