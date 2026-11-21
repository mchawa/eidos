"""Scoped == Public Surface polyglot AST & inheritance parity validator (CMP-ARCHITECTURE-BOUNDARY-VALIDATOR)."""

import ast
import contextlib
import os
import re
from pathlib import Path
from typing import Any, ClassVar

import orjson
from ast_grep_py import SgRoot

from eidos.core.index.ast_diff import (
    AST_DIFF_EXTS,
    _extract_python_dunder_all_from_source,
    _is_component_surface_symbol,
    _is_public_symbol,
)
from eidos.core.index.ast_indexer import get_ast_indexer
from eidos.core.index.ast_preprocess import _parse_python_ast
from eidos.core.spec.models import SpecDocument, ValidationReport
from eidos.core.workspace.project import STANDARD_EXCLUDED_DIRS, expand_brace_globs, is_dotnet_bin_dir


class PublicSurfaceValidator:
    """Verifies backticked == Public Surface symbols in active CNT-* and CMP-* specs against disk ASTs."""

    @staticmethod
    def _resolve_source_path(repo_root: Path, root_dir: Path, raw_path: str) -> Path | None:
        """Resolve a non-.adoc source path or glob pattern (including {a,b} brace globs) against repo_root or root_dir."""
        p = raw_path.strip()
        if not p or p.upper() in ("N/A", "TBD", "NONE") or p.endswith(".adoc"):
            return None
        for expanded in expand_brace_globs(p):
            if any(ch in expanded for ch in ("*", "?", "[")):
                matches = sorted(repo_root.glob(expanded)) or sorted(root_dir.glob(expanded))
                if matches:
                    return matches[0]
                continue
            for base in (repo_root, root_dir):
                cand = base / expanded
                if cand.exists():
                    return cand
        return None

    @staticmethod
    def _strip_asciidoc_comments(body: str) -> str:
        cleaned = re.sub(
            r"^[ \t]*/{4,}[ \t]*\r?\n(?:.*?\r?\n)?[ \t]*/{4,}[ \t]*$",
            "",
            body,
            flags=re.MULTILINE | re.DOTALL,
        )
        return re.sub(r"^[ \t]*//[^\r\n]*$", "", cleaned, flags=re.MULTILINE)

    _PUBLIC_SURFACE_CACHE_MAX_ENTRIES: ClassVar[int] = 8192
    _PUBLIC_SURFACE_EXTRACTOR_VERSION: ClassVar[str] = "1"

    _PUBLIC_SURFACE_SYMBOL_CACHE: ClassVar[
        dict[
            tuple[str, int, int],
            tuple[
                tuple[set[str], set[str], set[str], dict[str, set[str]], dict[str, set[str]]],
                tuple[set[str], set[str], set[str], dict[str, set[str]], dict[str, set[str]]],
            ],
        ]
    ] = {}
    _PENDING_PERSIST_ROWS: ClassVar[dict[str, tuple[str, int, int, str, str]]] = {}

    @staticmethod
    def _serialize_public_surface_entry(
        pub_result: tuple[set[str], set[str], set[str], dict[str, set[str]], dict[str, set[str]]],
        cmp_result: tuple[set[str], set[str], set[str], dict[str, set[str]], dict[str, set[str]]],
    ) -> str:
        def _pack(
            entry: tuple[set[str], set[str], set[str], dict[str, set[str]], dict[str, set[str]]],
        ) -> list[Any]:
            syms, clss, meths, cls_meths, bases = entry
            return [
                sorted(syms),
                sorted(clss),
                sorted(meths),
                {k: sorted(v) for k, v in sorted(cls_meths.items())},
                {k: sorted(v) for k, v in sorted(bases.items())},
            ]

        return orjson.dumps([_pack(pub_result), _pack(cmp_result)]).decode("utf-8")

    @staticmethod
    def _deserialize_public_surface_entry(
        payload_json: str,
    ) -> (
        tuple[
            tuple[set[str], set[str], set[str], dict[str, set[str]], dict[str, set[str]]],
            tuple[set[str], set[str], set[str], dict[str, set[str]], dict[str, set[str]]],
        ]
        | None
    ):
        try:
            raw = orjson.loads(payload_json)
            if not isinstance(raw, list) or len(raw) != 2:
                return None

            def _unpack(
                arr: Any,
            ) -> tuple[set[str], set[str], set[str], dict[str, set[str]], dict[str, set[str]]]:
                syms, clss, meths, cls_meths, bases = arr
                return (
                    set(syms),
                    set(clss),
                    set(meths),
                    {str(k): set(v) for k, v in cls_meths.items()},
                    {str(k): set(v) for k, v in bases.items()},
                )

            return _unpack(raw[0]), _unpack(raw[1])
        except (ValueError, TypeError, AttributeError):
            return None

    @classmethod
    def hydrate_persistent_cache(cls, db_path: Path) -> int:
        """Batch-hydrate the in-memory Public Surface LRU cache from SQLite public_surface_cache (non-blocking)."""
        from eidos.core.index.storage import load_public_surface_cache

        rows = load_public_surface_cache(db_path, extractor_version=cls._PUBLIC_SURFACE_EXTRACTOR_VERSION)
        loaded = 0
        for file_path, (mtime_ns, st_size, _ver, payload_json) in rows.items():
            cache_key = (file_path, mtime_ns, st_size)
            if cache_key in cls._PUBLIC_SURFACE_SYMBOL_CACHE:
                continue
            parsed = cls._deserialize_public_surface_entry(payload_json)
            if parsed is None:
                continue
            while len(cls._PUBLIC_SURFACE_SYMBOL_CACHE) >= cls._PUBLIC_SURFACE_CACHE_MAX_ENTRIES:
                oldest_key = next(iter(cls._PUBLIC_SURFACE_SYMBOL_CACHE))
                cls._PUBLIC_SURFACE_SYMBOL_CACHE.pop(oldest_key, None)
            cls._PUBLIC_SURFACE_SYMBOL_CACHE[cache_key] = parsed
            loaded += 1
        return loaded

    @classmethod
    def flush_persistent_cache(cls, db_path: Path) -> int:
        """Opportunistically persist newly extracted Public Surface entries to SQLite public_surface_cache."""
        if not cls._PENDING_PERSIST_ROWS:
            return 0
        from eidos.core.index.storage import save_public_surface_cache

        rows = list(cls._PENDING_PERSIST_ROWS.values())
        cls._PENDING_PERSIST_ROWS.clear()
        save_public_surface_cache(db_path, rows)
        return len(rows)

    _PUBLIC_SURFACE_SKIP_EXTENSIONS: ClassVar[tuple[str, ...]] = (
        ".md",
        ".toml",
        ".adoc",
        ".json",
        ".yaml",
        ".yml",
        ".py",
        ".ts",
        ".tsx",
        ".mts",
        ".cts",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".rs",
        ".go",
        ".cs",
        ".csproj",
        ".fsproj",
        ".vbproj",
        ".sln",
        ".props",
        ".targets",
        ".config",
        ".xml",
        ".xsd",
        ".wsdl",
        ".svc",
        ".aspx",
        ".ascx",
        ".ashx",
        ".asmx",
        ".cshtml",
        ".razor",
        ".rdlc",
        ".resx",
        ".dll",
        ".exe",
        ".pdb",
        ".proto",
        ".graphql",
        ".gql",
        ".java",
        ".kt",
        ".kts",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".rb",
        ".php",
        ".swift",
        ".vue",
        ".svelte",
        ".sql",
        ".sh",
        ".ps1",
        ".bat",
        ".cmd",
        ".css",
        ".scss",
        ".html",
        ".svg",
        ".png",
        ".pdf",
        ".lock",
        ".env",
        ".ini",
        ".cfg",
        ".txt",
        ".csv",
        ".db",
        ".sqlite",
        ".onnx",
    )

    _PUBLIC_SURFACE_NON_SYMBOL_KEYWORDS: ClassVar[frozenset[str]] = frozenset(
        {
            "true",
            "false",
            "null",
            "None",
            "True",
            "False",
            "void",
            "bool",
            "boolean",
            "int",
            "int8",
            "int16",
            "int32",
            "int64",
            "uint",
            "uint32",
            "uint64",
            "long",
            "short",
            "byte",
            "sbyte",
            "float",
            "double",
            "decimal",
            "string",
            "str",
            "char",
            "object",
            "dynamic",
            "any",
            "never",
            "unknown",
            "undefined",
            "self",
            "cls",
            "this",
            "GET",
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
            "HEAD",
            "OPTIONS",
            "SOAP",
            "REST",
            "HTTP",
            "HTTPS",
            "JSON",
            "XML",
            "GRPC",
            "WCF",
        }
    )

    @staticmethod
    def _extract_ast_base_name(base_node: ast.AST) -> str | None:
        if isinstance(base_node, ast.Name):
            return base_node.id
        if isinstance(base_node, ast.Attribute):
            return base_node.attr
        if isinstance(base_node, ast.Subscript):
            return PublicSurfaceValidator._extract_ast_base_name(base_node.value)
        return None

    @staticmethod
    def _strip_balanced_angle_and_paren_args(text: str) -> str:
        """Strip balanced <...>, (...), and [...] type/call argument clauses from inheritance specs."""
        out: list[str] = []
        angle_depth = 0
        paren_depth = 0
        bracket_depth = 0
        for ch in text:
            if ch == "<":
                angle_depth += 1
            elif ch == ">" and angle_depth > 0:
                angle_depth -= 1
            elif ch == "(":
                paren_depth += 1
            elif ch == ")" and paren_depth > 0:
                paren_depth -= 1
            elif ch == "[":
                bracket_depth += 1
            elif ch == "]" and bracket_depth > 0:
                bracket_depth -= 1
            elif angle_depth == 0 and paren_depth == 0 and bracket_depth == 0:
                out.append(ch)
        return "".join(out)

    @classmethod
    def _extract_polyglot_base_classes(cls, source: str, suffix: str) -> dict[str, set[str]]:
        """Extract class/interface base type names across ':', 'extends', 'implements', and '<' syntax."""
        bases_by_class: dict[str, set[str]] = {}
        lang_map = {
            ".ts": "typescript",
            ".tsx": "tsx",
            ".mts": "typescript",
            ".cts": "typescript",
            ".js": "javascript",
            ".jsx": "jsx",
            ".mjs": "javascript",
            ".cjs": "javascript",
            ".rs": "rust",
            ".java": "java",
            ".kt": "kotlin",
            ".kts": "kotlin",
            ".cs": "csharp",
            ".cpp": "cpp",
            ".c": "c",
            ".h": "cpp",
            ".hpp": "cpp",
            ".swift": "swift",
            ".php": "php",
            ".rb": "ruby",
        }
        sg_lang = lang_map.get(suffix)
        if sg_lang and any(tok in source for tok in (":", "extends", "implements", "<")):
            cls_kinds = (
                "class_declaration",
                "interface_declaration",
                "struct_declaration",
                "record_declaration",
                "class_specifier",
                "struct_specifier",
                "protocol_declaration",
                "trait_item",
                "class",
            )
            base_clause_kinds = frozenset(
                {
                    "base_list",
                    "superclass",
                    "super_interfaces",
                    "extends_interfaces",
                    "class_heritage",
                    "extends_clause",
                    "implements_clause",
                    "extends_type_clause",
                    "delegation_specifier",
                    "base_class_clause",
                    "inheritance_specifier",
                    "base_clause",
                    "class_interface_clause",
                    "trait_bounds",
                }
            )
            with contextlib.suppress(RuntimeError, ValueError):
                root = SgRoot(source, sg_lang).root()
                for ck in cls_kinds:
                    with contextlib.suppress(RuntimeError, ValueError):
                        for cnode in root.find_all(kind=ck):
                            name_node = cnode.field("name")
                            if name_node is None:
                                name_node = next(
                                    (
                                        ch
                                        for ch in cnode.children()
                                        if ch.kind()
                                        in ("type_identifier", "simple_identifier", "identifier", "constant")
                                    ),
                                    None,
                                )
                            cname = name_node.text().strip() if name_node is not None else ""
                            if not cname:
                                continue
                            for ch in cnode.children():
                                if ch.kind() not in base_clause_kinds:
                                    continue
                                cleaned = cls._strip_balanced_angle_and_paren_args(ch.text())
                                for item in re.split(r"[,+&]", cleaned):
                                    stripped_item = re.sub(
                                        r"^(?::|<|\b(?:extends|implements|public|protected|private|internal|virtual|dyn)\b|\s)+",
                                        "",
                                        item.strip(),
                                    )
                                    if not stripped_item or stripped_item.startswith(("'", "?")):
                                        continue
                                    m_b = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*$", stripped_item)
                                    if m_b:
                                        bases_by_class.setdefault(cname, set()).add(m_b.group(1))
                if bases_by_class:
                    return bases_by_class

        for ext_m in re.finditer(
            r"\b(?:class|interface)\s+([A-Za-z_][A-Za-z0-9_]*)(?:\s*<[^>]*>)?\s+extends\s+([A-Za-z_][A-Za-z0-9_.]*)",
            source,
        ):
            bases_by_class.setdefault(ext_m.group(1), set()).add(ext_m.group(2).rsplit(".", 1)[-1])
        return bases_by_class

    @classmethod
    def _extract_cached_public_surface_symbols(
        cls,
        code_file: Path,
        *,
        include_component_internal: bool = True,
    ) -> tuple[set[str], set[str], set[str], dict[str, set[str]], dict[str, set[str]]]:
        """Return cached (defined_symbols, defined_classes, defined_methods, methods_by_class, base_classes_by_class)."""
        try:
            resolved_file = code_file.resolve()
            st = resolved_file.stat()
        except OSError:
            return set(), set(), set(), {}, {}

        cache_key = (str(resolved_file), st.st_mtime_ns, st.st_size)
        cached = cls._PUBLIC_SURFACE_SYMBOL_CACHE.get(cache_key)
        if cached is not None:
            cls._PUBLIC_SURFACE_SYMBOL_CACHE.pop(cache_key, None)
            cls._PUBLIC_SURFACE_SYMBOL_CACHE[cache_key] = cached
            return cached[1] if include_component_internal else cached[0]

        try:
            source = resolved_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return set(), set(), set(), {}, {}

        indexer = get_ast_indexer()
        pub_syms: set[str] = set()
        pub_clss: set[str] = set()
        pub_meths: set[str] = set()
        pub_cls_meths: dict[str, set[str]] = {}
        pub_bases: dict[str, set[str]] = {}

        cmp_syms: set[str] = set()
        cmp_clss: set[str] = set()
        cmp_meths: set[str] = set()
        cmp_cls_meths: dict[str, set[str]] = {}
        cmp_bases: dict[str, set[str]] = {}

        def _record_symbol(
            syms_out: set[str],
            clss_out: set[str],
            meths_out: set[str],
            cls_meths_out: dict[str, set[str]],
            sym_obj: Any,
        ) -> None:
            if sym_obj.name:
                syms_out.add(sym_obj.name)
            if sym_obj.kind in ("class", "struct", "interface", "trait", "enum") and sym_obj.name:
                clss_out.add(sym_obj.name)
                cls_meths_out.setdefault(sym_obj.name, set())
            if sym_obj.parent_symbol and sym_obj.name:
                p_leaf = sym_obj.parent_symbol.split("<", 1)[0].split("[", 1)[0].split("::")[-1].split(".")[-1].strip()
                if p_leaf:
                    syms_out.add(f"{p_leaf}.{sym_obj.name}")
                    meths_out.add(f".{sym_obj.name}")
                    clss_out.add(p_leaf)
                    cls_meths_out.setdefault(p_leaf, set()).add(sym_obj.name)

        extracted_syms = indexer.extract_symbols(resolved_file, content=source)
        interface_names = {s.name for s in extracted_syms if s.kind == "interface" and s.name}
        type_kinds = ("class", "struct", "interface", "trait", "enum")
        declared_type_names = {s.name for s in extracted_syms if s.kind in type_kinds and s.name}
        visible_cmp_classes = {
            s.name
            for s in extracted_syms
            if s.kind in type_kinds and s.name and _is_component_surface_symbol(s, interface_names=interface_names)
        }
        hidden_cmp_classes = declared_type_names - visible_cmp_classes
        for sym in extracted_syms:
            if _is_public_symbol(sym):
                _record_symbol(pub_syms, pub_clss, pub_meths, pub_cls_meths, sym)
            if _is_component_surface_symbol(
                sym,
                interface_names=interface_names,
                hidden_classes=hidden_cmp_classes,
            ):
                _record_symbol(cmp_syms, cmp_clss, cmp_meths, cmp_cls_meths, sym)

        suffix = resolved_file.suffix.lower()
        if suffix == ".py":
            py_dunder_all = _extract_python_dunder_all_from_source(source)
            with contextlib.suppress(SyntaxError, ValueError):
                tree = _parse_python_ast(source)

                def _py_top_visible(name: str) -> bool:
                    return not name.startswith("_")

                def _py_member_visible(cls_name: str, member_name: str) -> bool:
                    return not cls_name.startswith("_") and (
                        not member_name.startswith("_") or member_name in ("__init__", "__call__")
                    )

                def _py_is_container_top(name: str) -> bool:
                    if not _py_top_visible(name):
                        return False
                    return py_dunder_all is None or name in py_dunder_all

                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if _py_top_visible(node.name):
                            cmp_syms.add(node.name)
                            if _py_is_container_top(node.name):
                                pub_syms.add(node.name)
                    elif isinstance(node, ast.ClassDef):
                        if node.name.startswith("_"):
                            continue
                        cmp_syms.add(node.name)
                        cmp_clss.add(node.name)
                        c_meths = cmp_cls_meths.setdefault(node.name, set())
                        c_bases = cmp_bases.setdefault(node.name, set())
                        in_cnt_all = py_dunder_all is None or node.name in py_dunder_all
                        if in_cnt_all:
                            pub_syms.add(node.name)
                            pub_clss.add(node.name)
                            p_meths = pub_cls_meths.setdefault(node.name, set())
                            p_bases = pub_bases.setdefault(node.name, set())
                        else:
                            p_meths = None
                            p_bases = None
                        for base_expr in node.bases:
                            b_name = cls._extract_ast_base_name(base_expr)
                            if b_name:
                                c_bases.add(b_name)
                                if p_bases is not None:
                                    p_bases.add(b_name)
                        for item in node.body:
                            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                if _py_member_visible(node.name, item.name):
                                    cmp_syms.add(f"{node.name}.{item.name}")
                                    cmp_meths.add(f".{item.name}")
                                    c_meths.add(item.name)
                                    if p_meths is not None:
                                        pub_syms.add(f"{node.name}.{item.name}")
                                        pub_meths.add(f".{item.name}")
                                        p_meths.add(item.name)
                                for sub in ast.walk(item):
                                    if isinstance(sub, (ast.Assign, ast.AnnAssign)):
                                        targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
                                        for tgt in targets:
                                            if (
                                                isinstance(tgt, ast.Attribute)
                                                and isinstance(tgt.value, ast.Name)
                                                and tgt.value.id in ("self", "cls")
                                                and _py_member_visible(node.name, tgt.attr)
                                            ):
                                                cmp_syms.add(f"{node.name}.{tgt.attr}")
                                                cmp_meths.add(f".{tgt.attr}")
                                                c_meths.add(tgt.attr)
                                                if p_meths is not None:
                                                    pub_syms.add(f"{node.name}.{tgt.attr}")
                                                    pub_meths.add(f".{tgt.attr}")
                                                    p_meths.add(tgt.attr)
                            elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                                if _py_member_visible(node.name, item.target.id):
                                    cmp_syms.add(f"{node.name}.{item.target.id}")
                                    cmp_meths.add(f".{item.target.id}")
                                    c_meths.add(item.target.id)
                                    if p_meths is not None:
                                        pub_syms.add(f"{node.name}.{item.target.id}")
                                        pub_meths.add(f".{item.target.id}")
                                        p_meths.add(item.target.id)
                            elif isinstance(item, ast.Assign):
                                for t in item.targets:
                                    if isinstance(t, ast.Name) and _py_member_visible(node.name, t.id):
                                        cmp_syms.add(f"{node.name}.{t.id}")
                                        cmp_meths.add(f".{t.id}")
                                        c_meths.add(t.id)
                                        if p_meths is not None:
                                            pub_syms.add(f"{node.name}.{t.id}")
                                            pub_meths.add(f".{t.id}")
                                            p_meths.add(t.id)
                    elif isinstance(node, ast.Assign):
                        for t in node.targets:
                            if isinstance(t, ast.Name):
                                if _py_top_visible(t.id):
                                    cmp_syms.add(t.id)
                                    if _py_is_container_top(t.id):
                                        pub_syms.add(t.id)
                                if t.id == "__all__" and isinstance(node.value, (ast.List, ast.Tuple, ast.Set)):
                                    for elt in node.value.elts:
                                        if (
                                            isinstance(elt, ast.Constant)
                                            and isinstance(elt.value, str)
                                            and not elt.value.startswith("_")
                                        ):
                                            cmp_syms.add(elt.value)
                                            pub_syms.add(elt.value)
                    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                        if _py_top_visible(node.target.id):
                            cmp_syms.add(node.target.id)
                            if _py_is_container_top(node.target.id):
                                pub_syms.add(node.target.id)
                    elif isinstance(node, ast.ImportFrom):
                        for al in node.names:
                            aname = al.asname or al.name
                            if _py_top_visible(aname):
                                cmp_syms.add(aname)
                                if _py_is_container_top(aname):
                                    pub_syms.add(aname)
                    elif isinstance(node, ast.Import):
                        for al in node.names:
                            aname = al.asname or al.name.split(".")[0]
                            if _py_top_visible(aname):
                                cmp_syms.add(aname)
                                if _py_is_container_top(aname):
                                    pub_syms.add(aname)
        else:
            extracted_bases = cls._extract_polyglot_base_classes(source, suffix)
            for cn, bset in extracted_bases.items():
                cmp_bases.setdefault(cn, set()).update(bset)
                pub_bases.setdefault(cn, set()).update(bset)

        while len(cls._PUBLIC_SURFACE_SYMBOL_CACHE) >= cls._PUBLIC_SURFACE_CACHE_MAX_ENTRIES:
            oldest_key = next(iter(cls._PUBLIC_SURFACE_SYMBOL_CACHE))
            cls._PUBLIC_SURFACE_SYMBOL_CACHE.pop(oldest_key, None)

        pub_result = (pub_syms, pub_clss, pub_meths, pub_cls_meths, pub_bases)
        cmp_result = (cmp_syms, cmp_clss, cmp_meths, cmp_cls_meths, cmp_bases)
        cls._PUBLIC_SURFACE_SYMBOL_CACHE[cache_key] = (pub_result, cmp_result)
        cls._PENDING_PERSIST_ROWS[str(resolved_file)] = (
            str(resolved_file),
            st.st_mtime_ns,
            st.st_size,
            cls._PUBLIC_SURFACE_EXTRACTOR_VERSION,
            cls._serialize_public_surface_entry(pub_result, cmp_result),
        )
        return cmp_result if include_component_internal else pub_result

    @staticmethod
    def _strip_public_surface_generics(expr: str) -> str:
        """Strip generic type parameter brackets (<...> and [...]) from the leading symbol path before '(' or ':' or '='."""
        out: list[str] = []
        i = 0
        n = len(expr)
        while i < n:
            ch = expr[i]
            if ch in ("(", "=") or ch.isspace():
                out.append(expr[i:])
                break
            if ch == ":" and not (i + 1 < n and expr[i + 1] == ":"):
                out.append(expr[i:])
                break
            if ch == "<":
                depth = 1
                j = i + 1
                while j < n and depth > 0:
                    if expr[j] == "<":
                        depth += 1
                    elif expr[j] == ">":
                        depth -= 1
                    j += 1
                if depth == 0:
                    i = j
                    continue
            elif ch == "[":
                inner_start = i + 1
                while inner_start < n and expr[inner_start].isspace():
                    inner_start += 1
                if inner_start < n and expr[inner_start] not in (
                    "'",
                    '"',
                    "0",
                    "1",
                    "2",
                    "3",
                    "4",
                    "5",
                    "6",
                    "7",
                    "8",
                    "9",
                ):
                    depth = 1
                    j = i + 1
                    while j < n and depth > 0:
                        if expr[j] == "[":
                            depth += 1
                        elif expr[j] == "]":
                            depth -= 1
                        j += 1
                    if depth == 0:
                        i = j
                        continue
            out.append(ch)
            i += 1
        return "".join(out)

    @staticmethod
    def _iter_public_surface_code_files(directory: Path) -> list[Path]:
        """Walk ``directory`` returning AST source files while pruning build/cache/venv outputs."""
        found: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(str(directory)):
            dirnames[:] = sorted(
                d
                for d in dirnames
                if d not in STANDARD_EXCLUDED_DIRS
                and not d.startswith(".")
                and not is_dotnet_bin_dir(Path(dirpath) / d)
            )
            for fn in sorted(filenames):
                fp = Path(dirpath) / fn
                if fp.suffix.lower() in AST_DIFF_EXTS:
                    found.append(fp)
        return found

    @staticmethod
    def _split_list_item_head_and_prose(item_body: str) -> tuple[str, str]:
        """Split a list item into declaration head and trailing prose tail without matching separators inside backticks."""
        in_bt = False
        n = len(item_body)
        i = 0
        while i < n:
            ch = item_body[i]
            if ch == "`":
                in_bt = not in_bt
                i += 1
                continue
            if not in_bt and item_body.startswith((" \u2014 ", " \u2013 ", " -- ", " - ", ": "), i):
                return item_body[:i], item_body[i:]
            i += 1
        return item_body, ""

    @classmethod
    def _is_explicit_code_span(cls, s: str) -> bool:
        """Return True if a backtick span carries explicit code syntax rather than a bare single-word prose token."""
        if any(ch in s for ch in ("(", "<", "[", "=", ">")) or "::" in s:
            return True
        if "." in s and not s.lower().endswith(cls._PUBLIC_SURFACE_SKIP_EXTENSIONS):
            return True
        return bool(
            re.match(
                r"^(?:pub(?:\s*\([^)]*\))?|export|default|async|unsafe|const|static|abstract|readonly|override|virtual|inline|def|class|fn|func|function|struct|enum|trait|interface|type|let|var|impl|public|internal)\s+",
                s,
            )
        )

    @classmethod
    def _iter_public_surface_candidate_spans(cls, section_body: str) -> list[str]:
        """Extract symbol candidate backtick spans from '== Public Surface', filtering narrative prose tokens."""
        stripped_blocks = re.sub(
            r"(?ms)^(?:----|```)[^\r\n]*\r?\n.*?^(?:----|```)\s*$",
            "",
            section_body,
        )
        candidates: list[str] = []
        bt_re = re.compile(r"`([^`]+)`")

        def _maybe_add(raw_span: str, *, require_code_syntax: bool) -> None:
            s = raw_span.strip()
            if (
                not s
                or "/" in s
                or "\\" in s
                or s.startswith(("[", "-", '"', "'", "$", "@", "<"))
                or s in cls._PUBLIC_SURFACE_NON_SYMBOL_KEYWORDS
            ):
                return
            if "(" not in s and "::" not in s and s.lower().endswith(cls._PUBLIC_SURFACE_SKIP_EXTENSIONS):
                return
            if require_code_syntax and not cls._is_explicit_code_span(s):
                return
            candidates.append(s)

        for raw_line in stripped_blocks.splitlines():
            line = raw_line.strip()
            if not line or line == "|===" or (line.startswith(":") and ":" in line[1:]) or line.startswith("["):
                continue
            if re.match(r"^===+\s+", line):
                for m in bt_re.finditer(line):
                    _maybe_add(m.group(1), require_code_syntax=False)
                continue
            list_m = re.match(r"^(?:[*.-]+|\d+\.)\s+(.*)$", line)
            if list_m:
                head, tail = cls._split_list_item_head_and_prose(list_m.group(1))
                for m in bt_re.finditer(head):
                    _maybe_add(m.group(1), require_code_syntax=False)
                if tail:
                    for m in bt_re.finditer(tail):
                        _maybe_add(m.group(1), require_code_syntax=True)
                continue
            if line.startswith("|"):
                cells = line.split("|")[1:]
                if cells:
                    for m in bt_re.finditer(cells[0]):
                        _maybe_add(m.group(1), require_code_syntax=False)
                    for cell in cells[1:]:
                        for m in bt_re.finditer(cell):
                            _maybe_add(m.group(1), require_code_syntax=True)
                continue
            for m in bt_re.finditer(line):
                _maybe_add(m.group(1), require_code_syntax=True)

        return candidates

    _PROSE_CONTRAST_CUE_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"\b(?:replaces?|replaced|removed|renamed|instead\s+of|rather\s+than|unlike|deprecated|legacy|rejected|formerly|e\.g\.|for\s+example|such\s+as|never|does\s+not|not\s+exist)\b",
        re.IGNORECASE,
    )
    _PROSE_ENTITY_PREFIXES: ClassVar[frozenset[str]] = frozenset(
        {
            "SYS",
            "ARCH",
            "DOM",
            "CAP",
            "POL",
            "PER",
            "EXP",
            "CNT",
            "CMP",
            "CTR",
            "DS",
            "UIC",
            "SCR",
            "CLT",
            "FLOW",
            "DLT",
            "INV",
            "DEC",
        }
    )
    _PROSE_EXTERNAL_TYPES: ClassVar[frozenset[str]] = frozenset(
        {
            "Path",
            "Console",
            "Math",
            "Object",
            "String",
            "Date",
            "DateTime",
            "Task",
            "Guid",
            "Results",
            "HttpContext",
            "JsonSerializer",
            "Window",
            "Document",
            "Promise",
            "Array",
            "Map",
            "Set",
            "Error",
            "Response",
            "Request",
            "URL",
            "Process",
            "File",
            "Directory",
            "Environment",
            "Enum",
            "Protocol",
            "ABC",
            "Deno",
            "Router",
            "WebApplication",
            "BaseHTTPRequestHandler",
            "ThreadingHTTPServer",
            "EventSource",
            "URLSearchParams",
            "FormData",
        }
    )
    _PROSE_BUILTIN_CALLS: ClassVar[frozenset[str]] = frozenset(
        {
            "print",
            "len",
            "str",
            "int",
            "float",
            "bool",
            "list",
            "dict",
            "set",
            "tuple",
            "open",
            "range",
            "type",
            "isinstance",
            "getattr",
            "setattr",
            "hasattr",
            "super",
            "next",
            "iter",
            "sorted",
            "min",
            "max",
            "sum",
            "any",
            "all",
            "zip",
            "map",
            "filter",
            "enumerate",
            "format",
            "repr",
            "fetch",
            "require",
            "import",
            "export",
            "bm25",
            "Ok",
            "BadRequest",
            "NotFound",
            "Route",
            "Blueprint",
            "APIRouter",
            "FastAPI",
        }
    )

    @staticmethod
    def _new_repo_symbol_accumulator() -> dict[str, Any]:
        """Create a mutable accumulator for repo-wide AST symbols collected during validation."""
        return {
            "symbols": set(),
            "classes": set(),
            "methods": set(),
            "methods_by_class": {},
            "bases_by_class": {},
            "parsed_files": set(),
        }

    @classmethod
    def _accumulate_file_symbols(cls, code_file: Path, repo_symbol_acc: dict[str, Any]) -> None:
        """Add component-scoped AST symbols for ``code_file`` into ``repo_symbol_acc`` if not yet recorded."""
        resolved = code_file.resolve()
        if resolved in repo_symbol_acc["parsed_files"]:
            return
        repo_symbol_acc["parsed_files"].add(resolved)
        f_syms, f_clss, f_meths, f_cls_meths, f_cls_bases = cls._extract_cached_public_surface_symbols(
            resolved,
            include_component_internal=True,
        )
        repo_symbol_acc["symbols"].update(f_syms)
        repo_symbol_acc["classes"].update(f_clss)
        repo_symbol_acc["methods"].update(f_meths)
        for cn, mset in f_cls_meths.items():
            repo_symbol_acc["methods_by_class"].setdefault(cn, set()).update(mset)
        for cn, bset in f_cls_bases.items():
            repo_symbol_acc["bases_by_class"].setdefault(cn, set()).update(bset)

    def _resolve_entry_code_files(self, raw_sp: str, repo_root: Path, root_dir: Path) -> list[Path]:
        """Resolve a single source_path entry (file, directory, or glob/brace-glob) to deduplicated AST code files."""
        p = raw_sp.strip()
        if (
            not p
            or p.upper() in ("N/A", "TBD", "NONE")
            or p.endswith((".adoc", ".md", ".toml", ".yaml", ".yml", ".json"))
        ):
            return []
        found: list[Path] = []
        for expanded in expand_brace_globs(p):
            if any(ch in expanded for ch in ("*", "?", "[")):
                matches = sorted(repo_root.glob(expanded)) or sorted(root_dir.glob(expanded))
                for m_path in matches:
                    if m_path.is_file() and m_path.suffix.lower() in AST_DIFF_EXTS:
                        found.append(m_path)
                    elif m_path.is_dir():
                        found.extend(self._iter_public_surface_code_files(m_path))
                continue
            resolved = self._resolve_source_path(repo_root, root_dir, expanded)
            if resolved is None:
                continue
            if resolved.is_file() and resolved.suffix.lower() in AST_DIFF_EXTS:
                found.append(resolved)
            elif resolved.is_dir():
                found.extend(self._iter_public_surface_code_files(resolved))
        return list(dict.fromkeys(found))

    @staticmethod
    def _has_intra_component_import_link(files_a: list[Path], files_b: list[Path], repo_root: Path) -> bool:
        """Return True if any file in ``files_a`` and ``files_b`` are linked via imports or shared C#/Java/Go package."""
        indexer = get_ast_indexer()
        resolved_root = repo_root.resolve()

        def _build_keys(files: list[Path]) -> tuple[set[str], set[str], set[Path]]:
            keys: set[str] = set()
            namespaces: set[str] = set()
            go_dirs: set[Path] = set()
            for f in files:
                rf = f.resolve()
                stem = rf.stem
                if stem and stem != "__init__":
                    keys.add(stem)
                if rf.is_relative_to(resolved_root):
                    rel_no_ext = rf.relative_to(resolved_root).with_suffix("").as_posix()
                    parts = [pt for pt in rel_no_ext.split("/") if pt and pt != "__init__"]
                    for idx in range(len(parts)):
                        keys.add(".".join(parts[idx:]))
                        keys.add("/".join(parts[idx:]))
                if rf.suffix.lower() in (".cs", ".java", ".kt", ".kts"):
                    with contextlib.suppress(OSError, RuntimeError, ValueError):
                        namespaces.update(indexer.extract_declared_namespaces(rf))
                elif rf.suffix.lower() == ".go":
                    go_dirs.add(rf.parent)
            return keys, namespaces, go_dirs

        keys_a, ns_a, go_a = _build_keys(files_a)
        keys_b, ns_b, go_b = _build_keys(files_b)
        if (ns_a & ns_b) or (go_a & go_b):
            return True

        def _imports_match(src_files: list[Path], target_keys: set[str], target_ns: set[str]) -> bool:
            for sf in src_files:
                with contextlib.suppress(OSError, RuntimeError, ValueError):
                    for imp in indexer.extract_imports(sf):
                        clean = imp.strip().strip("\"'")
                        if not clean:
                            continue
                        if clean in target_keys or clean in target_ns:
                            return True
                        # Handle relative JS/TS/Py imports (./helper, ../helper, .helper) and member imports (pkg.mod.sym)
                        norm_slash = (
                            re.sub(r"^\.+/?", "", clean).rsplit(".", 1)[0]
                            if clean.endswith((".js", ".ts", ".mjs", ".cjs"))
                            else re.sub(r"^\.+/?", "", clean)
                        )
                        if norm_slash in target_keys:
                            return True
                        tail_dot = clean.rsplit(".", 1)[-1]
                        if tail_dot in target_keys:
                            return True
                        parent_dot = clean.rsplit(".", 1)[0] if "." in clean else ""
                        if parent_dot and (parent_dot in target_keys or parent_dot in target_ns):
                            return True
            return False

        return _imports_match(files_a, keys_b, ns_b) or _imports_match(files_b, keys_a, ns_a)

    def _verify_entity_public_surface(
        self,
        doc: SpecDocument,
        repo_root: Path,
        root_dir: Path,
        report: ValidationReport,
        *,
        repo_symbol_acc: dict[str, Any] | None = None,
    ) -> None:
        """Verify that backticked code symbols under '== Public Surface' in CMP-*/CNT-* specs exist in their source_path."""
        clean_body = self._strip_asciidoc_comments(doc.body or "")
        sec_m = re.search(
            r"^==\s+Public Surface\b[^\r\n]*\r?\n(.*?)(?=^==\s+|\Z)",
            clean_body,
            re.MULTILINE | re.DOTALL,
        )
        if not sec_m:
            return

        entry_files: list[tuple[str, list[Path]]] = []
        code_files: list[Path] = []
        for raw_sp in doc.model.source_paths():
            ef = self._resolve_entry_code_files(raw_sp, repo_root, root_dir)
            entry_files.append((raw_sp, ef))
            code_files.extend(ef)

        if not code_files:
            return

        include_cmp_internal = doc.doc_type == "tech/component"
        defined_symbols: set[str] = set()
        defined_classes: set[str] = set()
        defined_methods: set[str] = set()
        methods_by_class: dict[str, set[str]] = {}
        base_classes_by_class: dict[str, set[str]] = {}
        file_symbol_map: dict[Path, tuple[set[str], set[str], dict[str, set[str]]]] = {}
        for cf in dict.fromkeys(code_files):
            f_syms, f_clss, f_meths, f_cls_meths, f_cls_bases = self._extract_cached_public_surface_symbols(
                cf,
                include_component_internal=include_cmp_internal,
            )
            file_symbol_map[cf] = (f_syms, f_clss, f_cls_meths)
            defined_symbols.update(f_syms)
            defined_classes.update(f_clss)
            defined_methods.update(f_meths)
            for cn, mset in f_cls_meths.items():
                methods_by_class.setdefault(cn, set()).update(mset)
            for cn, bset in f_cls_bases.items():
                base_classes_by_class.setdefault(cn, set()).update(bset)
            if repo_symbol_acc is not None:
                self._accumulate_file_symbols(cf, repo_symbol_acc)

        entry_symbol_index: dict[str, tuple[set[str], set[str], dict[str, set[str]]]] = {}
        for raw_sp, ef in entry_files:
            e_syms: set[str] = set()
            e_clss: set[str] = set()
            e_cls_meths: dict[str, set[str]] = {}
            for cf in ef:
                fs, fc, fcm = file_symbol_map[cf]
                e_syms.update(fs)
                e_clss.update(fc)
                for cn, mset in fcm.items():
                    e_cls_meths.setdefault(cn, set()).update(mset)
            entry_symbol_index[raw_sp] = (e_syms, e_clss, e_cls_meths)

        neutral_bases = {
            "object",
            "ABC",
            "Protocol",
            "Generic",
            "Object",
            "IDisposable",
            "IAsyncDisposable",
            "IEquatable",
            "IComparable",
            "ICloneable",
            "Exception",
            "Attribute",
            "Send",
            "Sync",
            "Sized",
            "Copy",
            "Clone",
            "Debug",
            "Default",
        }
        resolved_methods_by_class: dict[str, set[str]] = {}
        classes_with_external_bases: set[str] = set()
        for cn in defined_classes | set(methods_by_class) | set(base_classes_by_class):
            acc_meths = set(methods_by_class.get(cn, set()))
            stack = list(base_classes_by_class.get(cn, set()))
            seen_anc: set[str] = {cn}
            has_ext = False
            while stack:
                anc = stack.pop()
                if anc in seen_anc or anc in neutral_bases:
                    continue
                seen_anc.add(anc)
                if anc in defined_classes or anc in methods_by_class:
                    acc_meths.update(methods_by_class.get(anc, set()))
                    stack.extend(base_classes_by_class.get(anc, set()))
                else:
                    has_ext = True
            resolved_methods_by_class[cn] = acc_meths
            if has_ext:
                classes_with_external_bases.add(cn)

        def _class_has_member(cls_name: str, member_name: str) -> bool:
            if f"{cls_name}.{member_name}" in defined_symbols or member_name in resolved_methods_by_class.get(
                cls_name, set()
            ):
                return True
            return cls_name in classes_with_external_bases and f".{member_name}" in defined_methods

        def _entry_contributes_ident(raw_sp: str, ident: str) -> bool:
            e_syms, e_clss, e_cls_meths = entry_symbol_index[raw_sp]
            if ident in e_syms:
                return True
            if "::" in ident:
                penult, leaf = ident.split("::")[-2], ident.split("::")[-1]
                return penult in e_clss or leaf in e_syms or leaf in e_cls_meths.get(penult, set())
            if "." in ident:
                c_part, m_part = ident.split(".")[-2], ident.split(".")[-1]
                return c_part in e_clss or m_part in e_cls_meths.get(c_part, set())
            return False

        seen_missing: set[str] = set()
        matched_entries: set[str] = set()
        matched_surface_count = 0
        for s in self._iter_public_surface_candidate_spans(sec_m.group(1)):
            # Normalize Go receiver headers: func (s *Server) Start(...) -> Server.Start(...)
            go_m = re.match(
                r"^func\s*\(\s*(?:[A-Za-z_][A-Za-z0-9_]*\s+)?\*?\s*([A-Za-z_][A-Za-z0-9_]*)(?:\[[^\]]*\])?\s*\)\s*([A-Za-z_][A-Za-z0-9_]*.*)$",
                s,
            )
            if go_m:
                s = f"{go_m.group(1)}.{go_m.group(2)}"
            else:
                while True:
                    next_s = re.sub(
                        r"^(?:pub(?:\s*\([^)]*\))?|export|default|async|unsafe|const|static|abstract|readonly|override|virtual|inline|public|internal|sealed|partial|record|event|extern(?:\s+\"[^\"]*\")?|def|class|fn|func|function|struct|enum|trait|interface|type|let|var|impl)\s+",
                        "",
                        s,
                    )
                    if next_s == s:
                        break
                    s = next_s
            s = self._strip_public_surface_generics(s)
            ms = re.match(
                r"^([A-Za-z_][A-Za-z0-9_]*(?:(?:\.|::)[A-Za-z_][A-Za-z0-9_]*)*)\s*(?:\(|:(?!:)|=|$)",
                s,
            )
            if not ms:
                continue
            ident = ms.group(1)
            if ident in seen_missing:
                continue
            exists = ident in defined_symbols
            if not exists and "::" in ident:
                ns_parts = ident.split("::")
                penult, leaf = ns_parts[-2], ns_parts[-1]
                if penult[0].isupper() or penult in defined_classes or penult in methods_by_class:
                    exists = (penult in defined_symbols or penult in defined_classes) and _class_has_member(
                        penult, leaf
                    )
                else:
                    exists = leaf in defined_symbols
            elif not exists and "." in ident:
                dot_parts = ident.split(".")
                c_part, m_part = dot_parts[-2], dot_parts[-1]
                if c_part in defined_classes or c_part in methods_by_class:
                    exists = _class_has_member(c_part, m_part)
            if not exists:
                seen_missing.add(ident)
                kind_label = "Component" if doc.doc_type == "tech/component" else "Container"
                report.broken_references.append(
                    f"Public surface drift in {doc.file_path.name}: {kind_label} '{doc.doc_id}' documents symbol '{ident}' under '== Public Surface' which does not exist in its source_path"
                )
            else:
                matched_surface_count += 1
                for raw_sp, ef in entry_files:
                    if ef and _entry_contributes_ident(raw_sp, ident):
                        matched_entries.add(raw_sp)

        # Advisory Check 1 (DLT-2026-143): flag code-bearing source_path entries on multi-entry CMP-* specs
        # that contribute 0 == Public Surface symbols, have no intra-component import linkage, and are not
        # explicitly cited in the component specification body.
        code_bearing_entries = [(raw_sp, ef) for raw_sp, ef in entry_files if ef]
        if doc.doc_type == "tech/component" and len(code_bearing_entries) >= 2 and matched_surface_count > 0:
            for raw_sp, ef in code_bearing_entries:
                if raw_sp in matched_entries:
                    continue
                other_files = [f for sp, fs in code_bearing_entries if sp != raw_sp for f in fs]
                if self._has_intra_component_import_link(ef, other_files, repo_root):
                    continue
                entry_path_tokens = {raw_sp.strip(), Path(raw_sp.strip()).name}
                entry_path_tokens.update(cf.name for cf in ef)
                if any(tok and tok in clean_body for tok in entry_path_tokens):
                    continue
                report.warnings.append(
                    f"Over-scoped source_path in {doc.file_path.name}: Component '{doc.doc_id}' includes '{raw_sp}' which contributes 0 symbols to '== Public Surface' and has no intra-component import linkage to its other source_path entries"
                )

    @classmethod
    def _extract_prose_symbol_candidates(cls, doc: SpecDocument) -> list[tuple[str, str, str, str, bool]]:
        """Extract high-precision explicit code references (Type.member, Type::member, func(...)) outside '== Public Surface'."""
        clean_body = cls._strip_asciidoc_comments(doc.body or "")
        without_ps = re.sub(
            r"^==\s+Public Surface\b[^\r\n]*\r?\n.*?(?=^==\s+|\Z)",
            "",
            clean_body,
            flags=re.MULTILINE | re.DOTALL,
        )
        stripped = re.sub(
            r"(?ms)^(?:----|```)[^\r\n]*\r?\n.*?^(?:----|```)\s*$",
            "",
            without_ps,
        )
        candidates: list[tuple[str, str, str, str, bool]] = []
        seen_display: set[str] = set()
        bt_re = re.compile(r"`([^`]+)`")
        qual_re = re.compile(r"^([A-Z][A-Za-z0-9_]*)(?:\.|::)([A-Za-z][A-Za-z0-9_]*)(?:\s*(\())?")
        call_re = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)\([^`]*\)$")

        for raw_line in stripped.splitlines():
            line = raw_line.strip()
            if not line or line.startswith(("==", "|===", "[", ":")):
                continue
            for m in bt_re.finditer(raw_line):
                span = m.group(1).strip()
                if (
                    not span
                    or "/" in span
                    or "\\" in span
                    or "://" in span
                    or span.startswith(("-", "@", "$", "#", "[", "<", "'", '"'))
                    or span in cls._PUBLIC_SURFACE_NON_SYMBOL_KEYWORDS
                ):
                    continue
                if "(" not in span and "::" not in span and span.lower().endswith(cls._PUBLIC_SURFACE_SKIP_EXTENSIONS):
                    continue
                clause_before = re.split(r"[;!?]|(?<!\be)(?<!\bg)\.\s+", raw_line[: m.start()])[-1]
                if cls._PROSE_CONTRAST_CUE_RE.search(clause_before):
                    continue

                m_qual = qual_re.match(span)
                if m_qual:
                    cls_part, mem_part, has_open_paren = m_qual.group(1), m_qual.group(2), bool(m_qual.group(3))
                    if cls_part in cls._PROSE_ENTITY_PREFIXES:
                        continue
                    # Ensure span is strictly a qualified member or call (no trailing sentence prose).
                    rest = span[m_qual.end() :].strip()
                    if has_open_paren and not rest.endswith(")"):
                        continue
                    if not has_open_paren and rest:
                        continue
                    display = f"{cls_part}.{mem_part}()" if has_open_paren else f"{cls_part}.{mem_part}"
                    if display not in seen_display:
                        seen_display.add(display)
                        candidates.append(("qualified", display, cls_part, mem_part, has_open_paren))
                    continue

                m_call = call_re.match(span)
                if m_call:
                    fn_name = m_call.group(1)
                    if fn_name in cls._PROSE_BUILTIN_CALLS or fn_name.isupper():
                        continue
                    display = f"{fn_name}()"
                    if display not in seen_display:
                        seen_display.add(display)
                        candidates.append(("call", display, "", fn_name, True))
        return candidates

    @classmethod
    def _prose_candidate_resolved(
        cls,
        kind: str,
        cls_part: str,
        mem_part: str,
        has_call: bool,
        repo_symbol_acc: dict[str, Any],
    ) -> bool:
        """Return True if an explicit prose symbol candidate exists in (or is an external/stdlib type relative to) repo_symbol_acc."""
        symbols: set[str] = repo_symbol_acc["symbols"]
        classes: set[str] = repo_symbol_acc["classes"]
        methods: set[str] = repo_symbol_acc["methods"]
        methods_by_class: dict[str, set[str]] = repo_symbol_acc["methods_by_class"]
        bases_by_class: dict[str, set[str]] = repo_symbol_acc["bases_by_class"]

        if kind == "call":
            return mem_part in symbols or f".{mem_part}" in methods

        if cls_part in classes or cls_part in methods_by_class:
            if f"{cls_part}.{mem_part}" in symbols or mem_part in methods_by_class.get(cls_part, set()):
                return True
            # Walk known in-repo ancestors for inherited members.
            stack = list(bases_by_class.get(cls_part, set()))
            seen: set[str] = {cls_part}
            has_ext_base = False
            while stack:
                anc = stack.pop()
                if anc in seen:
                    continue
                seen.add(anc)
                if anc in classes or anc in methods_by_class:
                    if mem_part in methods_by_class.get(anc, set()) or f"{anc}.{mem_part}" in symbols:
                        return True
                    stack.extend(bases_by_class.get(anc, set()))
                else:
                    has_ext_base = True
            return has_ext_base and f".{mem_part}" in methods

        # Type not defined in-repo: skip known external/stdlib or imported types, and skip non-call dotted prose.
        return bool(cls_part in cls._PROSE_EXTERNAL_TYPES or cls_part in symbols or not has_call)

    def _verify_prose_symbol_drift(
        self,
        docs: list[SpecDocument],
        repo_root: Path,
        root_dir: Path,
        report: ValidationReport,
        repo_symbol_acc: dict[str, Any],
    ) -> None:
        """Conservatively verify explicit prose code references outside '== Public Surface' via 2-tier lazy AST lookup (DLT-2026-143)."""
        pending: list[tuple[SpecDocument, str, str, str, str, bool]] = []
        for doc in docs:
            for kind, display, cls_part, mem_part, has_call in self._extract_prose_symbol_candidates(doc):
                if not self._prose_candidate_resolved(kind, cls_part, mem_part, has_call, repo_symbol_acc):
                    pending.append((doc, kind, display, cls_part, mem_part, has_call))

        if not pending:
            return

        # Tier 2 lazy expansion: only parse remaining declared CNT-*/CMP-* files when unresolved candidates remain.
        for doc in docs:
            if not pending:
                break
            for raw_sp in doc.model.source_paths():
                for cf in self._resolve_entry_code_files(raw_sp, repo_root, root_dir):
                    if cf.resolve() in repo_symbol_acc["parsed_files"]:
                        continue
                    self._accumulate_file_symbols(cf, repo_symbol_acc)
                    pending = [
                        item
                        for item in pending
                        if not self._prose_candidate_resolved(item[1], item[3], item[4], item[5], repo_symbol_acc)
                    ]
                    if not pending:
                        break
                if not pending:
                    break

        for doc, _kind, display, _cls_part, _mem_part, _has_call in pending:
            kind_label = "Component" if doc.doc_type == "tech/component" else "Container"
            report.warnings.append(
                f"Prose symbol drift in {doc.file_path.name}: {kind_label} '{doc.doc_id}' references '{display}' outside '== Public Surface' which does not exist in the repository AST symbol catalog"
            )
