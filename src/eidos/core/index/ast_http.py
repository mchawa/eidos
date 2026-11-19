"""Static polyglot HTTP server endpoint and client request AST extractor (CMP-HTTP-TOPOLOGY-LINKER)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from eidos.core.index.ast_preprocess import (
    CodeSymbol,
    _extract_sfc_script_blocks,
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
    """deterministically evaluate a literal, template/f-string, concatenation, constant, or prefix helper call."""
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
    indexer: Any,
    p: Path,
    content: str,
    *,
    _symbols: list[CodeSymbol] | None = None,
) -> list[CodeSymbol]:
    suffix = p.suffix.lower()
    rel_posix = p.as_posix()
    supported = {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".mts",
        ".cts",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".kts",
        ".cs",
    }
    if suffix not in supported or not content:
        return []

    is_next_route = bool(_NEXT_APP_ROUTE_RE.search(rel_posix))
    if not is_next_route:
        if suffix == ".py" and "@" not in content and "Route(" not in content:
            return []
        if suffix in (
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".mjs",
            ".cjs",
            ".mts",
            ".cts",
        ) and not _TS_HTTP_ENDPOINT_HINT_RE.search(content):
            return []
        if (
            suffix == ".go"
            and "HandleFunc" not in content
            and ".GET(" not in content
            and ".POST(" not in content
            and ".PUT(" not in content
            and ".PATCH(" not in content
            and ".DELETE(" not in content
            and ".Get(" not in content
            and ".Post(" not in content
            and ".Put(" not in content
            and ".Patch(" not in content
            and ".Delete(" not in content
        ):
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
    indexer: Any,
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

        ts_clients = {
            "axios": "",
            "ky": "",
            "this.http": "",
            "this.httpClient": "",
            "http": "",
            "httpClient": "",
            **clients,
        }
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
