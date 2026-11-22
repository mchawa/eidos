"""Source-backed contract parity verifiers (code_interface, trpc, websocket, mcp, polyglot cli_args) (CMP-CONTRACT-PARITY-VALIDATOR)."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from eidos.core.index.ast_http import _parse_cs_authorize_args
from eidos.core.spec.cli_contract_parity import verify_cli_contract_commands
from eidos.core.spec.models import SpecDocument, ValidationReport
from eidos.core.spec.parser import YamlError, safe_load_yaml
from eidos.core.spec.schema_contract_parity import (
    CONTRAST_CUE_RE,
    check_forward_and_reverse_symbols,
    collect_protocol_source_files,
    format_spec_target_label,
    iter_contract_interface_sections,
    strip_asciidoc_comments,
)

_CODE_IFACE_EXTS = frozenset({".ts", ".tsx", ".d.ts", ".mts", ".cts", ".cs", ".go", ".rs", ".java", ".kt", ".kts"})
_TRPC_EXTS = frozenset({".ts", ".tsx", ".mts", ".cts"})
_WS_EXTS = frozenset({".cs", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".py"})
_MCP_EXTS = frozenset({".json", ".yaml", ".yml", ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts"})
_CLI_EXTS = frozenset({".py", ".go", ".rs", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".cs"})

_WS_LIFECYCLE_EVENTS = frozenset(
    {
        "connection",
        "disconnect",
        "disconnecting",
        "connect",
        "connect_error",
        "error",
        "close",
        "open",
        "listening",
        "upgrade",
    }
)

_TS_IFACE_RE = re.compile(r"\bexport\s+(?:declare\s+)?(?:interface|type)\s+([A-Za-z_][A-Za-z0-9_]*)")
_CS_IFACE_RE = re.compile(r"\bpublic\s+(?:partial\s+)?interface\s+([A-Za-z_][A-Za-z0-9_]*)")
_GO_IFACE_RE = re.compile(r"\btype\s+([A-Za-z_][A-Za-z0-9_]*)\s+interface\s*\{")
_RS_TRAIT_RE = re.compile(r"\bpub\s+trait\s+([A-Za-z_][A-Za-z0-9_]*)")
_JVM_IFACE_RE = re.compile(r"\b(?:public\s+)?interface\s+([A-Za-z_][A-Za-z0-9_]*)")
_METHOD_STOPWORDS = frozenset(
    {"if", "for", "while", "switch", "catch", "return", "new", "typeof", "sizeof", "constructor"}
)

_TRPC_PROC_RE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*)\s*:\s*[^,\n;{}]*?\.(query|mutation|subscription)\s*\(",
    re.DOTALL,
)
_TRPC_ROUTER_HINT_RE = re.compile(r"\b(?:createTRPCRouter|router)\s*\(\s*\{")

_CS_SIGNALR_CLASS_RE = re.compile(
    r"((?:\s*\[[^\]]+\]\s*)*)\bpublic\s+(?:partial\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*Hub(?:<[^>]+>)?\b"
)
_CS_HUB_METHOD_RE = re.compile(
    r"((?:\s*\[[^\]]+\]\s*)*)\bpublic\s+(?:async\s+)?(?:virtual\s+|override\s+)?[A-Za-z0-9_<>,?\[\].\s]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("
)
_CS_SIGNALR_LIFECYCLE = frozenset({"OnConnectedAsync", "OnDisconnectedAsync", "Dispose"})
_NEST_SUB_MSG_RE = re.compile(r"@SubscribeMessage\s*\(\s*[\"']([^\"']+)[\"']\s*\)")
_SOCKET_IO_EVENT_RE = re.compile(r"\b(?:socket|io|ws|client|server)\.(?:on|emit)\s*\(\s*[\"']([^\"']+)[\"']")

_COBRA_USE_RE = re.compile(r'\bUse\s*:\s*"([A-Za-z0-9_:-]+)(?:\s[^"]*)?"')
_COBRA_FLAG_RE = re.compile(r'(?:Persistent)?Flags\(\)\.\w+\(\s*(?:&[^,]+,\s*)?"([A-Za-z0-9_-]+)"')
_CLAP_SUBCMD_ENUM_RE = re.compile(
    r"#\[derive\([^)]*\bSubcommand\b[^)]*\)\]\s*(?:pub\s+)?enum\s+[A-Za-z_][A-Za-z0-9_]*\s*\{",
    re.DOTALL,
)
_CLAP_CMD_NEW_RE = re.compile(r'\bCommand::new\(\s*"([A-Za-z0-9_-]+)"\s*\)')
_CLAP_ARG_LONG_RE = re.compile(r"#\[arg\([^)]*\blong\b[^)]*\)\]\s*(?:pub\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*:")
_JS_CLI_CMD_RE = re.compile(r"""\.command\(\s*["']([A-Za-z0-9_:-]+)(?:\s[^"']*)?["']""")
_JS_CLI_OPT_RE = re.compile(r"""\.(?:option|requiredOption)\(\s*["']([^"']+)["']""")
_CS_CLI_CMD_RE = re.compile(r'\bnew\s+(?:Root)?Command\s*\(\s*"([A-Za-z0-9_:-]+)"')
_CS_CLI_OPT_RE = re.compile(r'\bnew\s+Option(?:<[^>]+>)?\s*\(\s*"(--?[A-Za-z0-9_-]+)"')


def _strip_c_comments(text: str) -> str:
    cleaned = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"//[^\r\n]*", "", cleaned)


def _extract_brace_body(text: str, open_brace_idx: int) -> str:
    depth = 0
    for idx in range(open_brace_idx, len(text)):
        ch = text[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace_idx + 1 : idx]
    return ""


def _camel_to_kebab(name: str) -> str:
    s1 = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", name)
    return re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1-\2", s1).lower()


def _extract_interface_methods(body_chunk: str, lang: str) -> list[str]:
    methods: list[str] = []
    if lang == "rust":
        for m in re.finditer(r"\b(?:async\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*[<(]", body_chunk):
            methods.append(m.group(1))
    elif lang == "go":
        for m in re.finditer(r"^\s*([A-Z][A-Za-z0-9_]*)\s*\(", body_chunk, re.MULTILINE):
            methods.append(m.group(1))
    elif lang == "ts":
        for m in re.finditer(
            r"^\s*(?:readonly\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\??\s*(?:<[^>]*>)?\s*\(", body_chunk, re.MULTILINE
        ):
            if m.group(1) not in _METHOD_STOPWORDS:
                methods.append(m.group(1))
    else:
        for m in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*(?:<[^>]*>)?\s*\(", body_chunk):
            cand = m.group(1)
            if cand not in _METHOD_STOPWORDS and not cand.startswith("_"):
                methods.append(cand)
    return methods


def verify_code_interface_contract(doc: SpecDocument, spec_file: Path | list[Path], report: ValidationReport) -> None:
    """Verify bidirectional parity for polyglot code_interface contracts (TS, C#, Go, Rust, Java/Kotlin)."""
    source_files = collect_protocol_source_files(spec_file, _CODE_IFACE_EXTS)
    if not source_files:
        return

    defined_symbols: list[str] = []
    for path in source_files:
        try:
            text = _strip_c_comments(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        low = path.name.lower()
        if low.endswith((".ts", ".tsx", ".d.ts", ".mts", ".cts")):
            pattern, lang = _TS_IFACE_RE, "ts"
        elif low.endswith(".cs"):
            pattern, lang = _CS_IFACE_RE, "cs"
        elif low.endswith(".go"):
            pattern, lang = _GO_IFACE_RE, "go"
        elif low.endswith(".rs"):
            pattern, lang = _RS_TRAIT_RE, "rust"
        else:
            pattern, lang = _JVM_IFACE_RE, "jvm"

        for m in pattern.finditer(text):
            iface_name = m.group(1)
            defined_symbols.append(iface_name)
            brace_pos = text.find("{", m.end() - 1)
            if brace_pos != -1 and brace_pos - m.end() < 120:
                brace_body = _extract_brace_body(text, brace_pos)
                defined_symbols.extend(_extract_interface_methods(brace_body, lang))

    defined_symbols = list(dict.fromkeys(defined_symbols))
    if not defined_symbols:
        return

    body = strip_asciidoc_comments(doc.body or "")
    check_forward_and_reverse_symbols(
        doc,
        format_spec_target_label(spec_file),
        body,
        defined_symbols,
        entity_label="code interface symbol",
        table_header_re=r"\b(?:interface|trait|method|symbol|member)\b",
        backtick_prefixes=("interface", "trait", "method", "type"),
        report=report,
    )


def verify_trpc_contract(doc: SpecDocument, spec_file: Path | list[Path], report: ValidationReport) -> None:
    """Verify bidirectional parity for TypeScript tRPC router procedures."""
    source_files = collect_protocol_source_files(spec_file, _TRPC_EXTS)
    if not source_files:
        return

    procedures: list[str] = []
    for path in source_files:
        try:
            text = _strip_c_comments(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        router_starts = [m.end() - 1 for m in _TRPC_ROUTER_HINT_RE.finditer(text)]
        chunks = [_extract_brace_body(text, pos) for pos in router_starts] if router_starts else [text]
        for chunk in chunks:
            for m in _TRPC_PROC_RE.finditer(chunk):
                procedures.append(m.group(1))

    procedures = list(dict.fromkeys(procedures))
    if not procedures:
        return

    body = strip_asciidoc_comments(doc.body or "")
    check_forward_and_reverse_symbols(
        doc,
        format_spec_target_label(spec_file),
        body,
        procedures,
        entity_label="tRPC procedure",
        table_header_re=r"\b(?:procedure|endpoint|query|mutation|subscription)\b",
        backtick_prefixes=("query", "mutation", "subscription", "procedure"),
        report=report,
    )


def _extract_cs_attr_auth(attr_block: str) -> tuple[str | None, tuple[str, ...], tuple[str, ...]]:
    if re.search(r"\[\s*AllowAnonymous\s*\]", attr_block):
        return ("allow_anonymous", (), ())
    auth_matches = list(re.finditer(r"\[\s*Authorize(?:\s*\(([^)]*)\))?\s*\]", attr_block))
    if not auth_matches:
        return (None, (), ())
    policies: list[str] = []
    roles: list[str] = []
    for am in auth_matches:
        args = (am.group(1) or "").strip()
        if args:
            p_list, r_list = _parse_cs_authorize_args(args)
            policies.extend(p_list)
            roles.extend(r_list)
    return ("authorize", tuple(dict.fromkeys(policies)), tuple(dict.fromkeys(roles)))


def verify_websocket_contract(
    doc: SpecDocument,
    spec_file: Path | list[Path],
    report: ValidationReport,
    *,
    parse_doc_auth: Callable[[str, str], tuple[str, tuple[str, ...], tuple[str, ...]] | None] | None = None,
) -> None:
    """Verify bidirectional parity for SignalR hubs (with opt-in auth checks), NestJS @SubscribeMessage, and Socket.IO/WS events."""
    source_files = collect_protocol_source_files(spec_file, _WS_EXTS)
    if not source_files:
        return

    defined_events: list[str] = []
    hub_method_auth: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {}

    for path in source_files:
        try:
            text = _strip_c_comments(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        suffix = path.suffix.lower()
        if suffix == ".cs":
            for m_cls in _CS_SIGNALR_CLASS_RE.finditer(text):
                cls_auth = _extract_cs_attr_auth(m_cls.group(1) or "")
                brace_pos = text.find("{", m_cls.end())
                if brace_pos == -1:
                    continue
                cls_body = _extract_brace_body(text, brace_pos)
                for m_meth in _CS_HUB_METHOD_RE.finditer(cls_body):
                    mname = m_meth.group(2)
                    if mname in _CS_SIGNALR_LIFECYCLE or mname in _METHOD_STOPWORDS:
                        continue
                    defined_events.append(mname)
                    m_auth = _extract_cs_attr_auth(m_meth.group(1) or "")
                    effective = m_auth if m_auth[0] is not None else cls_auth
                    if effective[0] is not None:
                        hub_method_auth[mname] = (effective[0], effective[1], effective[2])
        else:
            for m_ns in _NEST_SUB_MSG_RE.finditer(text):
                defined_events.append(m_ns.group(1).strip())
            for m_io in _SOCKET_IO_EVENT_RE.finditer(text):
                ev = m_io.group(1).strip()
                if ev and ev.lower() not in _WS_LIFECYCLE_EVENTS:
                    defined_events.append(ev)

    defined_events = list(dict.fromkeys(defined_events))
    if not defined_events:
        return

    target_label = format_spec_target_label(spec_file)
    body = strip_asciidoc_comments(doc.body or "")
    check_forward_and_reverse_symbols(
        doc,
        target_label,
        body,
        defined_events,
        entity_label="WebSocket event/method",
        table_header_re=r"\b(?:event|hub|message|method)\b",
        backtick_prefixes=("event", "hub", "message"),
        report=report,
    )

    # Opt-in contradiction-only authorization parity for SignalR Hub methods
    if hub_method_auth and parse_doc_auth is not None:
        auth_header_re = re.compile(r"\b(?:auth|authorization|security|access)\b", re.IGNORECASE)
        for sec_chunk in iter_contract_interface_sections(body):
            in_table = False
            auth_col_idx: int | None = None
            header_seen = False
            for raw_line in sec_chunk.splitlines():
                stripped = raw_line.strip()
                if not stripped:
                    continue
                if stripped.startswith("|==="):
                    in_table = not in_table
                    auth_col_idx = None
                    header_seen = False
                    continue
                auth_cell = ""
                if stripped.startswith("|"):
                    cells = [c.strip().strip("`*") for c in stripped.split("|")[1:]]
                    if in_table and not header_seen:
                        header_seen = True
                        for idx, c in enumerate(cells):
                            if auth_header_re.search(c):
                                auth_col_idx = idx
                                break
                        continue
                    if auth_col_idx is not None and auth_col_idx < len(cells):
                        auth_cell = cells[auth_col_idx]
                auth_exp = parse_doc_auth(raw_line, auth_cell)
                if auth_exp is None:
                    continue
                doc_mode, doc_pols, doc_roles = auth_exp
                for mname, (code_mode, code_pols, code_roles) in hub_method_auth.items():
                    if not re.search(rf"(?<![A-Za-z0-9_]){re.escape(mname)}(?![A-Za-z0-9_])", raw_line):
                        continue
                    if doc_mode == "authorize" and code_mode == "allow_anonymous":
                        report.broken_references.append(
                            f"Contract auth drift in {doc.file_path.name}: SignalR hub method '{mname}' documents authorization "
                            f"but code marks it [AllowAnonymous] in '{target_label}'"
                        )
                    elif doc_mode == "allow_anonymous" and code_mode == "authorize":
                        report.broken_references.append(
                            f"Contract auth drift in {doc.file_path.name}: SignalR hub method '{mname}' documents anonymous access "
                            f"but code requires [Authorize] in '{target_label}'"
                        )
                    elif doc_mode == "authorize" and code_mode == "authorize":
                        for pol in doc_pols:
                            if code_pols and pol not in code_pols:
                                report.broken_references.append(
                                    f"Contract auth drift in {doc.file_path.name}: SignalR hub method '{mname}' documents policy '{pol}' "
                                    f"not present on [Authorize] in '{target_label}'"
                                )
                        for role in doc_roles:
                            if code_roles and role not in code_roles:
                                report.broken_references.append(
                                    f"Contract auth drift in {doc.file_path.name}: SignalR hub method '{mname}' documents role '{role}' "
                                    f"not present on [Authorize] in '{target_label}'"
                                )


def extract_mcp_symbols_from_text(suffix: str, text: str) -> tuple[list[str], list[str], list[str]]:
    """Extract (tools, resources, prompts) from Python or JS/TS MCP server source code."""
    tools: list[str] = []
    resources: list[str] = []
    prompts: list[str] = []
    if suffix == ".py":
        for m_dec in re.finditer(
            r"@[A-Za-z_][A-Za-z0-9_.]*\.(tool|resource|prompt)\s*(?:\(([^)]*)\))?\s*\r?\n\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)",
            text,
        ):
            kind, args_s, fn_name = m_dec.group(1), m_dec.group(2) or "", m_dec.group(3)
            m_explicit = re.search(r"(?:(?:name|uri)\s*=\s*)?[\"']([A-Za-z0-9_./:{}-]+)[\"']", args_s)
            sym = m_explicit.group(1) if m_explicit else fn_name
            {"tool": tools, "resource": resources, "prompt": prompts}[kind].append(sym)
        for m_reg in re.finditer(
            r"\.(add_tool|register_tool|add_resource|register_resource|add_prompt|register_prompt)\s*\(\s*[\"']([A-Za-z0-9_./:{}-]+)[\"']",
            text,
        ):
            verb, sym = m_reg.group(1), m_reg.group(2)
            if "tool" in verb:
                tools.append(sym)
            elif "resource" in verb:
                resources.append(sym)
            else:
                prompts.append(sym)
    else:
        for m_ts in re.finditer(
            r"\.(tool|registerTool|resource|registerResource|prompt|registerPrompt)\s*\(\s*[\"']([A-Za-z0-9_./:{}-]+)[\"']",
            text,
        ):
            verb, sym = m_ts.group(1), m_ts.group(2)
            if "tool" in verb.lower():
                tools.append(sym)
            elif "resource" in verb.lower():
                resources.append(sym)
            else:
                prompts.append(sym)
    return tools, resources, prompts


def verify_mcp_contract(doc: SpecDocument, spec_file: Path | list[Path], report: ValidationReport) -> None:
    """Verify transport-agnostic MCP contracts across JSON/YAML manifests and Python/TypeScript servers (tools, resources, prompts)."""
    source_files = collect_protocol_source_files(spec_file, _MCP_EXTS)
    if not source_files:
        return

    all_symbols: list[str] = []
    for path in source_files:
        suffix = path.suffix.lower()
        if suffix in (".json", ".yaml", ".yml"):
            try:
                data = safe_load_yaml(path.read_text(encoding="utf-8"))
            except (OSError, YamlError) as exc:
                report.broken_references.append(
                    f"Invalid MCP specification_file in {doc.file_path.name}: '{path.name}' failed to parse ({exc})"
                )
                return
            if not isinstance(data, dict):
                report.broken_references.append(
                    f"Invalid MCP specification_file in {doc.file_path.name}: '{path.name}' root must be a mapping"
                )
                return
            for section, name_keys in (
                ("tools", ("name",)),
                ("resources", ("uri", "name")),
                ("prompts", ("name",)),
            ):
                entries = data.get(section)
                if isinstance(entries, list):
                    for entry in entries:
                        if isinstance(entry, dict):
                            for nk in name_keys:
                                val = entry.get(nk)
                                if isinstance(val, str) and val.strip():
                                    all_symbols.append(val.strip())
                                    break
        else:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            tools, resources, prompts = extract_mcp_symbols_from_text(suffix, text)
            all_symbols.extend([*tools, *resources, *prompts])

    all_symbols = list(dict.fromkeys(all_symbols))
    if not all_symbols:
        return

    body = strip_asciidoc_comments(doc.body or "")
    check_forward_and_reverse_symbols(
        doc,
        format_spec_target_label(spec_file),
        body,
        all_symbols,
        entity_label="MCP capability",
        table_header_re=r"\b(?:tool|resource|prompt|method|capability)\b",
        backtick_prefixes=("tool", "resource", "prompt"),
        token_pattern=r"[A-Za-z0-9_./:{}-]+",
        report=report,
    )


def verify_polyglot_cli_contract(doc: SpecDocument, spec_file: Path | list[Path], report: ValidationReport) -> None:
    """Verify cli_args contracts across Python directories and Go (cobra), Rust (clap), Node/TS (commander/yargs), and C# (System.CommandLine)."""
    source_files = collect_protocol_source_files(spec_file, _CLI_EXTS)
    if not source_files:
        return

    py_files = [p for p in source_files if p.suffix.lower() == ".py"]
    other_files = [p for p in source_files if p.suffix.lower() != ".py"]

    # Safely delegate Python CLI entrypoints without ever calling read_text on a directory
    for py_file in py_files:
        verify_cli_contract_commands(doc, py_file, report)

    if not other_files:
        return

    commands: list[str] = []
    flags: list[str] = []

    for path in other_files:
        try:
            text = _strip_c_comments(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        suffix = path.suffix.lower()
        if suffix == ".go":
            for m in _COBRA_USE_RE.finditer(text):
                commands.append(m.group(1))
            for m_f in _COBRA_FLAG_RE.finditer(text):
                flags.append(f"--{m_f.group(1)}")
        elif suffix == ".rs":
            for m_enum in _CLAP_SUBCMD_ENUM_RE.finditer(text):
                enum_body = _extract_brace_body(text, m_enum.end() - 1)
                for raw_line in enum_body.splitlines():
                    stripped = raw_line.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    m_var = re.match(r"^([A-Z][A-Za-z0-9_]*)\b", stripped)
                    if m_var:
                        commands.append(_camel_to_kebab(m_var.group(1)))
            for m_new in _CLAP_CMD_NEW_RE.finditer(text):
                commands.append(m_new.group(1))
            for m_arg in _CLAP_ARG_LONG_RE.finditer(text):
                flags.append(f"--{m_arg.group(1).replace('_', '-')}")
        elif suffix in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts"):
            for m_cmd in _JS_CLI_CMD_RE.finditer(text):
                commands.append(m_cmd.group(1))
            for m_opt in _JS_CLI_OPT_RE.finditer(text):
                for m_long in re.finditer(r"--([A-Za-z0-9_-]+)", m_opt.group(1)):
                    flags.append(f"--{m_long.group(1)}")
        elif suffix == ".cs":
            for m_cmd in _CS_CLI_CMD_RE.finditer(text):
                commands.append(m_cmd.group(1))
            for m_opt in _CS_CLI_OPT_RE.finditer(text):
                flags.append(m_opt.group(1))

    commands = list(dict.fromkeys(commands))
    flags = list(dict.fromkeys(flags))
    if not commands:
        return

    target_label = format_spec_target_label(spec_file)
    body = strip_asciidoc_comments(doc.body or "")
    allowed = set(commands) | set(flags)
    check_forward_and_reverse_symbols(
        doc,
        target_label,
        body,
        commands,
        allowed_symbols=allowed,
        entity_label="CLI command",
        table_header_re=r"\b(?:command|subcommand)\b",
        backtick_prefixes=("command", "subcommand"),
        report=report,
    )

    # Also verify extracted long flags forward (when documented via CLI flags) and reverse on `--flag` tokens
    for flg in flags:
        if not re.search(rf"(?<![A-Za-z0-9_-]){re.escape(flg)}(?![A-Za-z0-9_-])", body):
            report.broken_references.append(
                f"Contract drift in {doc.file_path.name}: CLI flag '{flg}' defined in '{target_label}' "
                f"is not documented in contract '{doc.doc_id}'"
            )
    if flags:
        flag_set = set(flags)
        seen_bad_flag: set[str] = set()
        for sec_chunk in iter_contract_interface_sections(body):
            for raw_line in sec_chunk.splitlines():
                for m_flg in re.finditer(r"`(--[A-Za-z][A-Za-z0-9_-]*)`", raw_line):
                    clause_before = re.split(r"[.;!?]", raw_line[: m_flg.start()])[-1]
                    if CONTRAST_CUE_RE.search(clause_before):
                        continue
                    cand_flg = m_flg.group(1)
                    if cand_flg not in flag_set and cand_flg not in seen_bad_flag:
                        seen_bad_flag.add(cand_flg)
                        report.broken_references.append(
                            f"Contract drift in {doc.file_path.name}: documented CLI flag '{cand_flg}' in contract '{doc.doc_id}' "
                            f"does not exist in '{target_label}'"
                        )
