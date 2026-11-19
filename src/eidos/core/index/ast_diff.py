"""Structural public/boundary symbol diffing and frontend design change classifier (CMP-AST-DIFF-CLASSIFIER)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ast_grep_py import SgRoot

from eidos.core.index.ast_indexer import get_ast_indexer
from eidos.core.index.ast_preprocess import (
    _RUST_LIFETIME_AT_START_RE,
    CodeSymbol,
    _extract_python_dunder_all_from_source,
    _extract_sfc_script_blocks,
    _extract_sg_node_name,
    _extract_ts_exported_names,
    _is_boundary_export_symbol,
    _is_public_symbol,
    _strip_leading_decorators_from_signature,
)
from eidos.core.index.ast_preprocess import (
    _is_component_surface_symbol as _is_component_surface_symbol,
)

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
                    pname = _extract_sg_node_name(decl, ts_lang)
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
