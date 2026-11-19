"""AST data models, source preprocessing (comment/macro/SFC normalization), and symbol visibility helpers (CMP-AST-SYMBOL-INDEXER)."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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


_BLOCK_COMMENT_EXTS = frozenset(
    {
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
    }
)
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
                is_doc = raw.startswith("/*!") or (raw.startswith("/**") and len(raw) > 3 and raw[3] not in "*/")
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


_C_CONTROL_KEYWORDS: frozenset[str] = frozenset(
    {"if", "for", "while", "switch", "return", "sizeof", "catch", "else", "do"}
)


def _extract_sg_node_name(node: Any, lang: str = "") -> str | None:
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
                return name_txt if name_txt and name_txt not in _C_CONTROL_KEYWORDS else None
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
                n = _extract_sg_node_name(ch, lang)
                if n:
                    exports.add(n)
            elif ck == "ambient_declaration":
                for amb_ch in ch.children():
                    ack = amb_ch.kind()
                    if ack in decl_kinds:
                        n = _extract_sg_node_name(amb_ch, lang)
                        if n:
                            exports.add(n)
                    elif ack in ("lexical_declaration", "variable_declaration"):
                        for vd in amb_ch.find_all(kind="variable_declarator"):
                            n = _extract_sg_node_name(vd, lang)
                            if n:
                                exports.add(n)
            elif ck in ("lexical_declaration", "variable_declaration"):
                for vd in ch.find_all(kind="variable_declarator"):
                    n = _extract_sg_node_name(vd, lang)
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
                sym.parent_symbol is None or class_is_public is None or class_is_public.get(sym.parent_symbol, False)
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


def _parse_python_ast(source: str) -> ast.Module:
    """Parse Python source into an AST, normalizing PEP 695/696 type aliases and type parameters on Python >=3.11."""
    root = SgRoot(source, "python").root()
    edits: list[tuple[int, int, str]] = []
    for ta in root.find_all(kind="type_alias_statement"):
        left = ta.field("left")
        alias_ident = left.text().split("[", 1)[0].strip() if left is not None else ""
        if alias_ident.isidentifier():
            rng = ta.range()
            s_idx, e_idx = rng.start.index, rng.end.index
            nl_pad = "\n" * source[s_idx:e_idx].count("\n")
            edits.append((s_idx, e_idx, f"{alias_ident} = None{nl_pad}"))
    for tp in root.find_all(kind="type_parameter"):
        parent = tp.parent()
        if parent is not None and parent.kind() in ("class_definition", "function_definition"):
            rng = tp.range()
            edits.append((rng.start.index, rng.end.index, ""))
    if edits:
        edits.sort(key=lambda item: item[0], reverse=True)
        parts: list[str] = []
        cursor = len(source)
        for s_idx, e_idx, repl in edits:
            if e_idx <= cursor:
                parts.append(source[e_idx:cursor])
                parts.append(repl)
                cursor = s_idx
        parts.append(source[:cursor])
        source = "".join(reversed(parts))
    return ast.parse(source)
