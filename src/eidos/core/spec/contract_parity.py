"""Multi-protocol bidirectional CTR-* interface contract parity validator (CMP-CONTRACT-PARITY-VALIDATOR)."""

import ast
import os
import re
from pathlib import Path
from typing import ClassVar

from eidos.core.index.ast_http import (
    HttpEndpointMeta,
    _extract_fastapi_auth_deps,
    _parse_cs_authorize_args,
    _parse_nestjs_auth_decorators,
    _parse_spring_auth_annotations,
    collect_aspnet_core_controller_bases,
    extract_http_endpoints_with_metadata,
    normalize_http_route_path,
)
from eidos.core.index.ast_indexer import get_ast_indexer
from eidos.core.spec.cli_contract_parity import _parse_python_ast, verify_cli_contract_commands
from eidos.core.spec.models import SpecDocument, ValidationReport
from eidos.core.spec.parser import YamlError, safe_load_yaml
from eidos.core.spec.schema_contract_parity import (
    check_forward_and_reverse_symbols,
    verify_avro_contract,
    verify_sql_ddl_contract,
    verify_wsdl_contract,
)
from eidos.core.spec.source_contract_parity import (
    verify_code_interface_contract,
    verify_mcp_contract,
    verify_polyglot_cli_contract,
    verify_trpc_contract,
    verify_websocket_contract,
)
from eidos.core.workspace.project import STANDARD_EXCLUDED_DIRS

_HTTP_VERBS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
_REST_HTTP_SOURCE_EXTS = frozenset(
    {
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
)
_REST_HTTP_SKIP_DIRS = STANDARD_EXCLUDED_DIRS | {"bin", "obj"}
_NON_IFACE_HEADING_RE = re.compile(
    r"\b(?:architectural\s+boundary|boundary|context|background|non[- ]goals?|out[- ]of[- ]scope|exclusions?|excluded|notes?|operational\s+notes|design\s+constraints|quality\s+attributes|migration|deprecated|history|rationale|alternatives?)\b",
    re.IGNORECASE,
)
_CONTRAST_CUE_RE = re.compile(
    r"\b(?:unlike|out[- ]of[- ]scope|exclud(?:ed|es|ing)|instead\s+of|rather\s+than|does\s+not\s+expose|not\s+exposed|do\s+not\s+(?:call|expose|use)|not\s+part\s+of|handled\s+by|owned\s+by|belongs?\s+to|delegated\s+to|legacy|deprecated)\b",
    re.IGNORECASE,
)
_AUTH_HEADER_RE = re.compile(r"\b(?:auth|authorization|security|access)\b", re.IGNORECASE)
_AUTH_EXPLICIT_ALLOW_ANON_RE = re.compile(r"(?:\[\s*AllowAnonymous\s*\]|\bAllowAnonymous\b)")
_AUTH_EXPLICIT_AUTHORIZE_RE = re.compile(
    r"(?:\[\s*Authorize(?:\s*\(([^)]*)\))?\s*\]|\b(?:Authorize|RequireAuthorization)\s*\(([^)]*)\))"
)
_AUTH_COL_ANON_RE = re.compile(r"\b(?:anonymous|public|none|unauthenticated|allowanonymous)\b", re.IGNORECASE)
_AUTH_COL_AUTH_RE = re.compile(
    r"\b(?:authorize|authorized|authenticated|protected|required|bearer|jwt)\b", re.IGNORECASE
)


class ContractParityValidator:
    """Verifies bidirectional parity between CTR-* contract specs and specification_file targets across all 16 verifiable canonical protocols."""

    def _verify_cli_contract_commands(self, doc: SpecDocument, cli_file: Path, report: ValidationReport) -> None:
        verify_cli_contract_commands(doc, cli_file, report)

    @staticmethod
    def _strip_asciidoc_comments(body: str) -> str:
        """Strip AsciiDoc block comments (//// ... ////) and single-line comments (// ...) from contract body."""
        cleaned = re.sub(
            r"^[ \t]*/{4,}[ \t]*\r?\n(?:.*?\r?\n)?[ \t]*/{4,}[ \t]*$",
            "",
            body,
            flags=re.MULTILINE | re.DOTALL,
        )
        return re.sub(r"^[ \t]*//[^\r\n]*$", "", cleaned, flags=re.MULTILINE)

    def _verify_python_protocol_contract(self, doc: SpecDocument, spec_file: Path, report: ValidationReport) -> None:
        """Verify bidirectional parity between Python Protocol / ABC classes/methods in specification_file and the contract (DLT-2026-082)."""
        if spec_file.suffix != ".py":
            return
        try:
            tree = _parse_python_ast(spec_file.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError):
            return

        protocol_symbols: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                is_proto = any(
                    (isinstance(b, ast.Name) and b.id in ("Protocol", "ABC"))
                    or (isinstance(b, ast.Attribute) and b.attr in ("Protocol", "ABC"))
                    or (
                        isinstance(b, ast.Subscript)
                        and (
                            (isinstance(b.value, ast.Name) and b.value.id in ("Protocol", "ABC"))
                            or (isinstance(b.value, ast.Attribute) and b.value.attr in ("Protocol", "ABC"))
                        )
                    )
                    for b in node.bases
                )
                if is_proto:
                    protocol_symbols.append(node.name)
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and not item.name.startswith("_"):
                            protocol_symbols.append(item.name)

        protocol_symbols = list(dict.fromkeys(protocol_symbols))
        body = self._strip_asciidoc_comments(doc.body or "")
        for sym in protocol_symbols:
            if not re.search(rf"(?:`|\b){re.escape(sym)}\b", body):
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: Python protocol symbol '{sym}' defined in '{spec_file.name}' is not documented in contract '{doc.doc_id}'"
                )

        if protocol_symbols:
            proto_set = set(protocol_symbols)
            seen_doc_syms: set[str] = set()
            for m in re.finditer(
                r"`(?:(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\([^`]*\)|([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*(?:self\b|[A-Za-z_][A-Za-z0-9_]*\s*:\s*[A-Za-z_])[^`]*\))`",
                body,
            ):
                doc_sym = m.group(1) or m.group(2)
                if doc_sym and doc_sym not in proto_set and doc_sym not in seen_doc_syms:
                    seen_doc_syms.add(doc_sym)
                    report.broken_references.append(
                        f"Contract drift in {doc.file_path.name}: documented Python protocol symbol '{doc_sym}' in contract '{doc.doc_id}' does not exist in '{spec_file.name}'"
                    )

    def _verify_rest_openapi_contract(self, doc: SpecDocument, spec_file: Path, report: ValidationReport) -> None:
        """Verify bidirectional parity between OpenAPI specification_file paths/webhooks and documented routes in the contract."""
        try:
            data = safe_load_yaml(spec_file.read_text(encoding="utf-8"))
        except (OSError, YamlError) as exc:
            report.broken_references.append(
                f"Invalid OpenAPI specification_file in {doc.file_path.name}: '{spec_file.name}' failed to parse ({exc})"
            )
            return
        if not isinstance(data, dict):
            report.broken_references.append(
                f"Invalid OpenAPI specification_file in {doc.file_path.name}: '{spec_file.name}' root must be a mapping"
            )
            return
        paths = data.get("paths")
        webhooks = data.get("webhooks")
        has_paths = isinstance(paths, dict) and bool(paths)
        has_webhooks = isinstance(webhooks, dict) and bool(webhooks)
        if not has_paths and not has_webhooks:
            return
        body = self._strip_asciidoc_comments(doc.body or "")
        if has_paths:
            defined_routes = {str(k) for k in paths}
            for route in sorted(defined_routes):
                if not re.search(rf"(?<![A-Za-z0-9_./:~-]){re.escape(route)}(?=$|[\s`\"'|),;\]>])", body):
                    report.broken_references.append(
                        f"Contract drift in {doc.file_path.name}: OpenAPI path '{route}' defined in '{spec_file.name}' is not documented in contract '{doc.doc_id}'"
                    )

            sections_to_scan = self._iter_contract_interface_sections(body)

            doc_route_candidates: list[str] = []
            for sec_chunk in sections_to_scan:
                for raw_line in sec_chunk.splitlines():
                    stripped_line = raw_line.strip()
                    if stripped_line.startswith("|") and not stripped_line.startswith("|==="):
                        cells = [c.strip().strip("`*") for c in stripped_line.split("|")[1:]]
                        if any(c.upper() in _HTTP_VERBS for c in cells):
                            for cell in cells:
                                if re.fullmatch(r"/[A-Za-z0-9._~/{}:-]*", cell):
                                    doc_route_candidates.append(cell)
                    for m in re.finditer(
                        r"`\s*(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/[A-Za-z0-9._~/{}:-]*)(?:\s[^`\r\n]*)?`",
                        raw_line,
                    ):
                        if not stripped_line.startswith("|"):
                            clause_before = re.split(r"[.;!?]", raw_line[: m.start()])[-1]
                            if _CONTRAST_CUE_RE.search(clause_before):
                                continue
                        doc_route_candidates.append(m.group(1))

            seen_doc_routes: set[str] = set()
            for doc_route in doc_route_candidates:
                if "..." in doc_route or "*" in doc_route:
                    continue
                if doc_route not in defined_routes and doc_route not in seen_doc_routes:
                    seen_doc_routes.add(doc_route)
                    report.broken_references.append(
                        f"Contract drift in {doc.file_path.name}: documented OpenAPI path '{doc_route}' in contract '{doc.doc_id}' does not exist in '{spec_file.name}'"
                    )

        if has_webhooks:
            defined_webhooks = sorted(str(k) for k in webhooks)
            check_forward_and_reverse_symbols(
                doc,
                spec_file.name,
                body,
                defined_webhooks,
                entity_label="OpenAPI webhook",
                table_header_re=r"\bwebhook\b",
                backtick_prefixes=("webhook",),
                report=report,
            )

    @staticmethod
    def _collect_json_schema_allowed_names(node: object, out: set[str]) -> None:
        if isinstance(node, list):
            for item in node:
                ContractParityValidator._collect_json_schema_allowed_names(item, out)
            return
        if not isinstance(node, dict):
            return
        props = node.get("properties")
        if isinstance(props, dict):
            for k, v in props.items():
                out.add(str(k))
                ContractParityValidator._collect_json_schema_allowed_names(v, out)
        for def_key in ("$defs", "definitions"):
            defs = node.get(def_key)
            if isinstance(defs, dict):
                for k, v in defs.items():
                    out.add(str(k))
                    ContractParityValidator._collect_json_schema_allowed_names(v, out)
        for sub_key in ("items", "additionalProperties", "allOf", "anyOf", "oneOf", "then", "else"):
            if sub_key in node:
                ContractParityValidator._collect_json_schema_allowed_names(node[sub_key], out)

    def _verify_json_schema_contract(self, doc: SpecDocument, spec_file: Path, report: ValidationReport) -> None:
        """Verify bidirectional JSON Schema parity across top-level properties, $defs/definitions, and nested sub-schema properties."""
        try:
            data = safe_load_yaml(spec_file.read_text(encoding="utf-8"))
        except (OSError, YamlError) as exc:
            report.broken_references.append(
                f"Invalid JSON Schema specification_file in {doc.file_path.name}: '{spec_file.name}' failed to parse ({exc})"
            )
            return
        if not isinstance(data, dict):
            report.broken_references.append(
                f"Invalid JSON Schema specification_file in {doc.file_path.name}: '{spec_file.name}' root must be a mapping"
            )
            return
        props = data.get("properties")
        has_props = isinstance(props, dict) and bool(props)
        defs_names: list[str] = []
        for def_key in ("$defs", "definitions"):
            raw_defs = data.get(def_key)
            if isinstance(raw_defs, dict):
                defs_names.extend(str(k) for k in raw_defs)
        defs_names = list(dict.fromkeys(defs_names))

        if not has_props and not defs_names:
            return

        body = self._strip_asciidoc_comments(doc.body or "")
        if has_props:
            raw_req = data.get("required")
            req_source = raw_req if isinstance(raw_req, list) else props.keys()
            forward_targets = list(dict.fromkeys(str(p) for p in req_source if str(p) in props))
            entity_label = "JSON Schema property"
        else:
            forward_targets = defs_names
            entity_label = "JSON Schema definition"

        allowed_names: set[str] = set()
        self._collect_json_schema_allowed_names(data, allowed_names)

        check_forward_and_reverse_symbols(
            doc,
            spec_file.name,
            body,
            forward_targets,
            allowed_symbols=allowed_names,
            entity_label=entity_label,
            table_header_re=r"\b(?:property|field|definition)\b",
            backtick_prefixes=("property", "field", "def"),
            token_pattern=r"[A-Za-z_$][A-Za-z0-9_$-]*",
            report=report,
        )

    @staticmethod
    def _extract_proto_messages_and_enums(stripped_text: str) -> tuple[list[str], set[str]]:
        """Return (top_level_types, all_defined_types) for message and enum declarations in a .proto file."""
        top_level: list[str] = []
        all_types: set[str] = set()
        depth = 0
        token_re = re.compile(r"\b(message|enum)\s+([A-Za-z_][A-Za-z0-9_]*)\b|[{}]")
        for tok in token_re.finditer(stripped_text):
            raw = tok.group(0)
            if raw == "{":
                depth += 1
            elif raw == "}":
                depth = max(0, depth - 1)
            else:
                name = tok.group(2)
                if name:
                    all_types.add(name)
                    if depth == 0 and name not in top_level:
                        top_level.append(name)
        return top_level, all_types

    def _verify_grpc_proto_contract(self, doc: SpecDocument, spec_file: Path, report: ValidationReport) -> None:
        """Verify bidirectional parity between gRPC/Protobuf declarations in .proto specification_file and the contract (DLT-2026-082, DLT-2026-135)."""
        try:
            text = spec_file.read_text(encoding="utf-8")
        except OSError:
            return
        stripped_text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        stripped_text = re.sub(r"//[^\r\n]*", "", stripped_text)
        services = re.findall(r"\bservice\s+([A-Za-z_][A-Za-z0-9_]*)\b", stripped_text)
        rpcs = re.findall(r"\brpc\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", stripped_text)
        ordered_syms = list(dict.fromkeys([*services, *rpcs]))
        defined_syms = set(ordered_syms)
        body = self._strip_asciidoc_comments(doc.body or "")
        for sym in ordered_syms:
            if not re.search(rf"(?:`|\b){re.escape(sym)}\b", body):
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: gRPC symbol '{sym}' defined in '{spec_file.name}' is not documented in contract '{doc.doc_id}'"
                )

        seen_doc_syms: set[str] = set()
        for m in re.finditer(r"`\s*(?:rpc|service)\s+([A-Za-z_][A-Za-z0-9_]*)\b", body):
            doc_sym = m.group(1)
            if doc_sym not in defined_syms and doc_sym not in seen_doc_syms:
                seen_doc_syms.add(doc_sym)
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: documented gRPC symbol '{doc_sym}' in contract '{doc.doc_id}' does not exist in '{spec_file.name}'"
                )

        top_proto_types, all_proto_types = self._extract_proto_messages_and_enums(stripped_text)
        for p_sym in top_proto_types:
            if not re.search(rf"(?:`|\b){re.escape(p_sym)}\b", body):
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: Protobuf symbol '{p_sym}' defined in '{spec_file.name}' is not documented in contract '{doc.doc_id}'"
                )
        seen_proto_doc: set[str] = set()
        for pm in re.finditer(r"`\s*(?:message|enum)\s+([A-Za-z_][A-Za-z0-9_]*)\b", body):
            doc_p_sym = pm.group(1)
            if doc_p_sym not in all_proto_types and doc_p_sym not in seen_proto_doc:
                seen_proto_doc.add(doc_p_sym)
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: documented Protobuf symbol '{doc_p_sym}' in contract '{doc.doc_id}' does not exist in '{spec_file.name}'"
                )

    def _verify_graphql_contract(self, doc: SpecDocument, spec_file: Path, report: ValidationReport) -> None:
        """Verify bidirectional parity between root Query/Mutation/Subscription fields in GraphQL specification_file and the contract (DLT-2026-082)."""
        try:
            text = spec_file.read_text(encoding="utf-8")
        except OSError:
            return
        operations: list[str] = []
        for block_match in re.finditer(r"\btype\s+(?:Query|Mutation|Subscription)\s*\{([^}]*)\}", text, re.DOTALL):
            block = block_match.group(1)
            paren_depth = 0
            for line in block.splitlines():
                cleaned = line.split("#", 1)[0].strip()
                if not cleaned:
                    continue
                if paren_depth == 0:
                    m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(|:)", cleaned)
                    if m:
                        operations.append(m.group(1))
                paren_depth += cleaned.count("(") - cleaned.count(")")
                if paren_depth < 0:
                    paren_depth = 0
        operations = list(dict.fromkeys(operations))
        body = self._strip_asciidoc_comments(doc.body or "")
        defined_ops = set(operations)
        for op in operations:
            if not re.search(rf"(?:`|\b){re.escape(op)}\b", body):
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: GraphQL operation '{op}' defined in '{spec_file.name}' is not documented in contract '{doc.doc_id}'"
                )

        gql_keywords = {"type", "query", "mutation", "subscription", "input", "enum", "scalar", "interface", "union"}
        seen_doc_ops: set[str] = set()
        for m in re.finditer(
            r"`(?:(?:query|mutation|subscription)\s+([A-Za-z_][A-Za-z0-9_]*)\b[^`]*|([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*[A-Za-z_][A-Za-z0-9_]*\s*:\s*[A-Za-z_\[][^`]*\)(?:\s*:\s*[A-Za-z_\[][^`]*)?|([A-Za-z_][A-Za-z0-9_]*)\s*\([^`]*\)\s*:\s*[A-Za-z_\[][^`]*)`",
            body,
        ):
            doc_op = m.group(1) or m.group(2) or m.group(3)
            if (
                doc_op
                and doc_op.lower() not in gql_keywords
                and doc_op not in defined_ops
                and doc_op not in seen_doc_ops
            ):
                seen_doc_ops.add(doc_op)
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: documented GraphQL operation '{doc_op}' in contract '{doc.doc_id}' does not exist in '{spec_file.name}'"
                )

    @staticmethod
    def _iter_contract_interface_sections(body: str) -> list[str]:
        """Strip fenced code blocks and non-interface/contrastive H2 sections from a contract body."""
        stripped_for_rev = re.sub(
            r"(?ms)^(?:----|```)[^\r\n]*\r?\n.*?^(?:----|```)\s*$",
            "",
            body,
        )
        h2_matches = list(re.finditer(r"^==\s+([^\r\n]+)", stripped_for_rev, re.MULTILINE))
        sections_to_scan: list[str] = []
        if not h2_matches:
            sections_to_scan.append(stripped_for_rev)
        else:
            if h2_matches[0].start() > 0:
                sections_to_scan.append(stripped_for_rev[: h2_matches[0].start()])
            for idx_h, hm in enumerate(h2_matches):
                sec_end = h2_matches[idx_h + 1].start() if idx_h + 1 < len(h2_matches) else len(stripped_for_rev)
                heading_txt = hm.group(1).strip()
                if len(h2_matches) > 1 and _NON_IFACE_HEADING_RE.search(heading_txt):
                    continue
                sections_to_scan.append(stripped_for_rev[hm.end() : sec_end])
        return sections_to_scan

    @staticmethod
    def _collect_rest_http_source_files(spec_target: Path | list[Path]) -> list[Path]:
        """Expand a file, directory, or path list into deduplicated modern REST HTTP source files."""
        targets = [spec_target] if isinstance(spec_target, Path) else list(spec_target)
        files: list[Path] = []
        seen: set[Path] = set()
        for target in targets:
            if target.is_file():
                if target.suffix.lower() in _REST_HTTP_SOURCE_EXTS and target not in seen:
                    seen.add(target)
                    files.append(target)
            elif target.is_dir():
                for dirpath, dirnames, filenames in os.walk(target):
                    dirnames[:] = sorted(d for d in dirnames if d not in _REST_HTTP_SKIP_DIRS and not d.startswith("."))
                    for fname in sorted(filenames):
                        cand = Path(dirpath) / fname
                        if cand.suffix.lower() in _REST_HTTP_SOURCE_EXTS and cand not in seen:
                            seen.add(cand)
                            files.append(cand)
        return files

    @staticmethod
    def _parse_doc_auth_expectation(
        text: str,
        auth_cell: str = "",
    ) -> tuple[str, tuple[str, ...], tuple[str, ...]] | None:
        """Extract opt-in polyglot auth requirement (mode, policies, roles) from a contract route row or line."""
        if _AUTH_EXPLICIT_ALLOW_ANON_RE.search(text):
            return ("allow_anonymous", (), ())

        # Spring annotations (@PermitAll, @PreAuthorize, @Secured, @RolesAllowed)
        sp_anon, sp_auth, sp_pols, sp_roles = _parse_spring_auth_annotations(text)
        if sp_anon:
            return ("allow_anonymous", (), ())
        if sp_auth:
            return ("authorize", tuple(sp_pols), tuple(sp_roles))

        # NestJS decorators (@Public(), @UseGuards(...), @Roles(...))
        ns_pub, ns_auth, ns_guards, ns_roles = _parse_nestjs_auth_decorators(text)
        if ns_pub:
            return ("allow_anonymous", (), ())
        if ns_auth:
            return ("authorize", tuple(ns_guards), tuple(ns_roles))

        # FastAPI Security(...) / Depends(...)
        fa_deps = _extract_fastapi_auth_deps(text, require_auth_name=False)
        if fa_deps:
            return ("authorize", tuple(fa_deps), ())

        # ASP.NET Core [Authorize(...)] / RequireAuthorization(...)
        auth_matches = list(_AUTH_EXPLICIT_AUTHORIZE_RE.finditer(text))
        if auth_matches:
            policies: list[str] = []
            roles: list[str] = []
            for m in auth_matches:
                args_str = m.group(1) or m.group(2) or ""
                if args_str.strip():
                    p_list, r_list = _parse_cs_authorize_args(args_str)
                    policies.extend(p_list)
                    roles.extend(r_list)
            return ("authorize", tuple(dict.fromkeys(policies)), tuple(dict.fromkeys(roles)))
        if auth_cell:
            if _AUTH_COL_ANON_RE.search(auth_cell):
                return ("allow_anonymous", (), ())
            if _AUTH_COL_AUTH_RE.search(auth_cell):
                return ("authorize", (), ())
        return None

    def _extract_documented_rest_http_routes(
        self,
        body: str,
    ) -> dict[tuple[str, str], tuple[str, tuple[str, ...], tuple[str, ...]] | None]:
        """Parse documented (METHOD, normalized_path) routes and opt-in auth expectations from a CTR-* body."""
        documented: dict[tuple[str, str], tuple[str, tuple[str, ...], tuple[str, ...]] | None] = {}

        def _record(
            method: str,
            raw_path: str,
            auth_exp: tuple[str, tuple[str, ...], tuple[str, ...]] | None,
        ) -> None:
            if "..." in raw_path or "*" in raw_path:
                return
            norm_p = normalize_http_route_path(raw_path)
            if not norm_p:
                return
            key = (method.upper(), norm_p)
            if key not in documented or (documented[key] is None and auth_exp is not None):
                documented[key] = auth_exp

        for sec_chunk in self._iter_contract_interface_sections(body):
            in_table = False
            auth_col_idx: int | None = None
            header_seen = False
            for raw_line in sec_chunk.splitlines():
                stripped_line = raw_line.strip()
                if stripped_line.startswith("|==="):
                    in_table = not in_table
                    auth_col_idx = None
                    header_seen = False
                    continue
                if stripped_line.startswith("|"):
                    cells = [c.strip() for c in stripped_line.split("|")[1:]]
                    clean_cells = [c.strip("`*") for c in cells]
                    if in_table and not header_seen:
                        header_seen = True
                        for idx, c in enumerate(clean_cells):
                            if _AUTH_HEADER_RE.search(c):
                                auth_col_idx = idx
                                break
                        # If this first row already looks like a header (no HTTP verb + path), skip to data rows.
                        if not any(re.fullmatch(r"/[A-Za-z0-9._~/{}:-]*", c) for c in clean_cells) and not re.search(
                            r"\b(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+/",
                            stripped_line,
                        ):
                            continue
                    auth_cell = (
                        clean_cells[auth_col_idx]
                        if auth_col_idx is not None and auth_col_idx < len(clean_cells)
                        else ""
                    )
                    row_auth = self._parse_doc_auth_expectation(stripped_line, auth_cell)

                    row_methods: list[str] = []
                    row_paths: list[str] = []
                    for cell in clean_cells:
                        m_combined = re.fullmatch(
                            r"(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/[A-Za-z0-9._~/{}:-]*)",
                            cell,
                        )
                        if m_combined:
                            _record(m_combined.group(1), m_combined.group(2), row_auth)
                            continue
                        if re.fullmatch(r"/[A-Za-z0-9._~/{}:-]*", cell):
                            row_paths.append(cell)
                            continue
                        cell_verbs = [tok for tok in re.findall(r"\b[A-Z]{3,7}\b", cell) if tok in _HTTP_VERBS]
                        if cell_verbs and re.fullmatch(r"[A-Za-z,\s/`*]+", cell):
                            row_methods.extend(cell_verbs)
                    for verb in row_methods:
                        for path_val in row_paths:
                            _record(verb, path_val, row_auth)
                    continue

                for m in re.finditer(
                    r"`\s*(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/[A-Za-z0-9._~/{}:-]*)(?:\s[^`\r\n]*)?`",
                    raw_line,
                ):
                    clause_before = re.split(r"[.;!?]", raw_line[: m.start()])[-1]
                    if _CONTRAST_CUE_RE.search(clause_before):
                        continue
                    line_auth = self._parse_doc_auth_expectation(raw_line[m.end() :])
                    _record(m.group(1), m.group(2), line_auth)
        return documented

    def _verify_rest_http_contract(
        self,
        doc: SpecDocument,
        spec_target: Path | list[Path],
        report: ValidationReport,
    ) -> None:
        """Verify bidirectional (METHOD, path) and opt-in ASP.NET Core auth parity for rest_http / http_json contracts (DLT-2026-142)."""
        source_files = self._collect_rest_http_source_files(spec_target)
        if not source_files:
            return
        display_label = (
            spec_target.name
            if isinstance(spec_target, Path)
            else str(doc.model.specification_file or source_files[0].name)
        )
        indexer = get_ast_indexer()
        file_contents: list[tuple[Path, str]] = []
        for sf in source_files:
            try:
                content = sf.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            file_contents.append((sf, content))

        cs_files = [(sf, content) for sf, content in file_contents if sf.suffix.lower() == ".cs"]
        cs_bases = collect_aspnet_core_controller_bases(cs_files) if cs_files else None

        code_endpoints: dict[tuple[str, str], list[HttpEndpointMeta]] = {}
        for sf, content in file_contents:
            for ep in extract_http_endpoints_with_metadata(indexer, sf, content, base_controllers=cs_bases):
                code_endpoints.setdefault((ep.method, ep.path), []).append(ep)

        # Zero-route fallback: skip reverse scans when 0 framework endpoints are extracted (e.g. custom/stdlib servers).
        if not code_endpoints:
            return

        body = self._strip_asciidoc_comments(doc.body or "")
        doc_routes = self._extract_documented_rest_http_routes(body)

        for method, norm_path in sorted(code_endpoints):
            if (method, norm_path) not in doc_routes:
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: REST HTTP endpoint '{method} {norm_path}' defined in '{display_label}' is not documented in contract '{doc.doc_id}'"
                )

        for method, norm_path in doc_routes:
            if (method, norm_path) not in code_endpoints:
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: documented REST HTTP endpoint '{method} {norm_path}' in contract '{doc.doc_id}' does not exist in '{display_label}'"
                )

        for route_key, auth_exp in doc_routes.items():
            if auth_exp is None or route_key not in code_endpoints:
                continue
            method, norm_path = route_key
            exp_mode, exp_policies, exp_roles = auth_exp
            cs_eps = [ep for ep in code_endpoints[route_key] if ep.auth_mode is not None]
            if not cs_eps:
                continue
            ep = cs_eps[0]
            if exp_mode == "authorize":
                if ep.auth_mode != "authorize":
                    report.broken_references.append(
                        f"Contract drift in {doc.file_path.name}: REST HTTP endpoint '{method} {norm_path}' in contract '{doc.doc_id}' documents [Authorize] but code in '{display_label}' has effective auth '{ep.auth_mode}'"
                    )
                    continue
                for pol in exp_policies:
                    if pol not in ep.auth_policies:
                        report.broken_references.append(
                            f"Contract drift in {doc.file_path.name}: REST HTTP endpoint '{method} {norm_path}' in contract '{doc.doc_id}' documents [Authorize(Policy = \"{pol}\")] not matched in '{display_label}'"
                        )
                for role in exp_roles:
                    if role not in ep.auth_roles:
                        report.broken_references.append(
                            f"Contract drift in {doc.file_path.name}: REST HTTP endpoint '{method} {norm_path}' in contract '{doc.doc_id}' documents [Authorize(Roles = \"{role}\")] not matched in '{display_label}'"
                        )
            elif exp_mode == "allow_anonymous" and ep.auth_mode == "authorize":
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: REST HTTP endpoint '{method} {norm_path}' in contract '{doc.doc_id}' documents [AllowAnonymous] but code in '{display_label}' enforces [Authorize]"
                )

    def _verify_asyncapi_contract(self, doc: SpecDocument, spec_file: Path, report: ValidationReport) -> None:
        """Verify bidirectional parity between AsyncAPI 2.x/3.x channels & operations and the contract (DLT-2026-147)."""
        try:
            data = safe_load_yaml(spec_file.read_text(encoding="utf-8"))
        except (OSError, YamlError) as exc:
            report.broken_references.append(
                f"Invalid AsyncAPI specification_file in {doc.file_path.name}: '{spec_file.name}' failed to parse ({exc})"
            )
            return
        if not isinstance(data, dict):
            report.broken_references.append(
                f"Invalid AsyncAPI specification_file in {doc.file_path.name}: '{spec_file.name}' root must be a mapping"
            )
            return

        channels_raw = data.get("channels")
        operations_raw = data.get("operations")
        defined_channels: list[tuple[str, str]] = []  # (canonical_display, alt_key)
        defined_ops: list[str] = []
        valid_channel_tokens: set[str] = set()

        if isinstance(channels_raw, dict):
            for ch_key, ch_val in channels_raw.items():
                key_s = str(ch_key).strip()
                if not key_s:
                    continue
                addr_s = (
                    str(ch_val.get("address")).strip()
                    if isinstance(ch_val, dict) and isinstance(ch_val.get("address"), str) and ch_val["address"].strip()
                    else key_s
                )
                defined_channels.append((addr_s, key_s))
                valid_channel_tokens.add(addr_s)
                valid_channel_tokens.add(key_s)
                if isinstance(ch_val, dict):
                    for verb_k in ("publish", "subscribe"):
                        verb_obj = ch_val.get(verb_k)
                        if isinstance(verb_obj, dict) and isinstance(verb_obj.get("operationId"), str):
                            op_id = verb_obj["operationId"].strip()
                            if op_id:
                                defined_ops.append(op_id)

        if isinstance(operations_raw, dict):
            for op_key in operations_raw:
                op_s = str(op_key).strip()
                if op_s:
                    defined_ops.append(op_s)

        defined_ops = list(dict.fromkeys(defined_ops))
        if not defined_channels and not defined_ops:
            return

        body = self._strip_asciidoc_comments(doc.body or "")
        for addr_s, key_s in defined_channels:
            matched = bool(
                re.search(rf"(?<![A-Za-z0-9_./:-]){re.escape(addr_s)}(?![A-Za-z0-9_./:-])", body)
                or (
                    key_s != addr_s and re.search(rf"(?<![A-Za-z0-9_./:-]){re.escape(key_s)}(?![A-Za-z0-9_./:-])", body)
                )
            )
            if not matched:
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: AsyncAPI channel '{addr_s}' defined in '{spec_file.name}' is not documented in contract '{doc.doc_id}'"
                )

        for op_name in defined_ops:
            if not re.search(rf"(?<![A-Za-z0-9_./:-]){re.escape(op_name)}(?![A-Za-z0-9_./:-])", body):
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: AsyncAPI operation '{op_name}' defined in '{spec_file.name}' is not documented in contract '{doc.doc_id}'"
                )

        defined_op_set = set(defined_ops)
        seen_phantom_ch: set[str] = set()
        seen_phantom_op: set[str] = set()
        for sec_chunk in self._iter_contract_interface_sections(body):
            for raw_line in sec_chunk.splitlines():
                for m_ch in re.finditer(
                    r"`\s*(?:channel|publish|subscribe|send|receive)\s+([A-Za-z0-9_./{}-]+)\s*`",
                    raw_line,
                    re.IGNORECASE,
                ):
                    clause_before = re.split(r"[.;!?]", raw_line[: m_ch.start()])[-1]
                    if _CONTRAST_CUE_RE.search(clause_before):
                        continue
                    cand = m_ch.group(1).strip()
                    if cand not in valid_channel_tokens and cand not in defined_op_set and cand not in seen_phantom_ch:
                        seen_phantom_ch.add(cand)
                        report.broken_references.append(
                            f"Contract drift in {doc.file_path.name}: documented AsyncAPI channel '{cand}' in contract '{doc.doc_id}' does not exist in '{spec_file.name}'"
                        )
                for m_op in re.finditer(
                    r"`\s*operation\s+([A-Za-z0-9_.-]+)\s*`",
                    raw_line,
                    re.IGNORECASE,
                ):
                    clause_before = re.split(r"[.;!?]", raw_line[: m_op.start()])[-1]
                    if _CONTRAST_CUE_RE.search(clause_before):
                        continue
                    cand = m_op.group(1).strip()
                    if cand not in defined_op_set and cand not in seen_phantom_op:
                        seen_phantom_op.add(cand)
                        report.broken_references.append(
                            f"Contract drift in {doc.file_path.name}: documented AsyncAPI operation '{cand}' in contract '{doc.doc_id}' does not exist in '{spec_file.name}'"
                        )

    def _verify_json_rpc_stdio_contract(self, doc: SpecDocument, spec_file: Path, report: ValidationReport) -> None:
        """Verify bidirectional parity between OpenRPC/JSON-RPC/MCP manifests or handler sources and the contract (DLT-2026-147)."""
        suffix = spec_file.suffix.lower()
        defined_methods: list[str] = []

        if suffix in (".yaml", ".yml", ".json"):
            try:
                data = safe_load_yaml(spec_file.read_text(encoding="utf-8"))
            except (OSError, YamlError) as exc:
                report.broken_references.append(
                    f"Invalid JSON-RPC specification_file in {doc.file_path.name}: '{spec_file.name}' failed to parse ({exc})"
                )
                return
            if not isinstance(data, dict):
                report.broken_references.append(
                    f"Invalid JSON-RPC specification_file in {doc.file_path.name}: '{spec_file.name}' root must be a mapping"
                )
                return
            raw_methods = data.get("methods")
            if isinstance(raw_methods, list):
                for entry in raw_methods:
                    if isinstance(entry, dict) and isinstance(entry.get("name"), str) and entry["name"].strip():
                        defined_methods.append(entry["name"].strip())
                    elif isinstance(entry, str) and entry.strip():
                        defined_methods.append(entry.strip())
            elif isinstance(raw_methods, dict):
                defined_methods.extend(str(k).strip() for k in raw_methods if str(k).strip())
            raw_tools = data.get("tools")
            if isinstance(raw_tools, list):
                for entry in raw_tools:
                    if isinstance(entry, dict) and isinstance(entry.get("name"), str) and entry["name"].strip():
                        defined_methods.append(entry["name"].strip())
        elif suffix in (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts"):
            try:
                text = spec_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return
            if suffix == ".py":
                # @mcp.tool("name") / @server.tool(name="name") / @mcp.tool()\nasync def func_name
                for m_dec in re.finditer(
                    r"@[A-Za-z_][A-Za-z0-9_.]*\.(?:tool|method|rpc_method)\s*(?:\(([^)]*)\))?\s*\r?\n\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)",
                    text,
                ):
                    args_s, fn_name = m_dec.group(1) or "", m_dec.group(2)
                    m_explicit = re.search(r"(?:name\s*=\s*)?[\"']([A-Za-z0-9_./:-]+)[\"']", args_s)
                    defined_methods.append(m_explicit.group(1) if m_explicit else fn_name)
                for m_reg in re.finditer(
                    r"\.(?:register_method|register_tool|add_tool)\s*\(\s*[\"']([A-Za-z0-9_./:-]+)[\"']",
                    text,
                ):
                    defined_methods.append(m_reg.group(1))
                for m_cmp in re.finditer(
                    r"\bmethod\s*==\s*[\"']([A-Za-z0-9_./:-]+)[\"']|\bcase\s+[\"']([A-Za-z0-9_./:-]+)[\"']\s*:",
                    text,
                ):
                    defined_methods.append(m_cmp.group(1) or m_cmp.group(2))
            else:
                for m_ts in re.finditer(
                    r"\.(?:tool|registerTool|method|registerMethod)\s*\(\s*[\"']([A-Za-z0-9_./:-]+)[\"']",
                    text,
                ):
                    defined_methods.append(m_ts.group(1))
                for m_cmp in re.finditer(
                    r"\bmethod\s*===?\s*[\"']([A-Za-z0-9_./:-]+)[\"']|\bcase\s+[\"']([A-Za-z0-9_./:-]+)[\"']\s*:",
                    text,
                ):
                    defined_methods.append(m_cmp.group(1) or m_cmp.group(2))
        else:
            return

        defined_methods = list(dict.fromkeys(defined_methods))
        # Zero-method fallback for custom dispatchers
        if not defined_methods:
            return

        body = self._strip_asciidoc_comments(doc.body or "")
        for meth in defined_methods:
            if not re.search(rf"(?<![A-Za-z0-9_./:-]){re.escape(meth)}(?![A-Za-z0-9_./:-])", body):
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: JSON-RPC method '{meth}' defined in '{spec_file.name}' is not documented in contract '{doc.doc_id}'"
                )

        defined_set = set(defined_methods)
        seen_phantom: set[str] = set()
        for sec_chunk in self._iter_contract_interface_sections(body):
            in_table = False
            method_col_idx: int | None = None
            header_seen = False
            for raw_line in sec_chunk.splitlines():
                stripped = raw_line.strip()
                if stripped.startswith("|==="):
                    in_table = not in_table
                    method_col_idx = None
                    header_seen = False
                    continue
                if stripped.startswith("|"):
                    cells = [c.strip().strip("`*") for c in stripped.split("|")[1:]]
                    if in_table and not header_seen:
                        header_seen = True
                        for idx, c in enumerate(cells):
                            if re.search(r"\b(?:method|tool|rpc)\b", c, re.IGNORECASE):
                                method_col_idx = idx
                                break
                        continue
                    if method_col_idx is not None and method_col_idx < len(cells):
                        cand = cells[method_col_idx].strip()
                        if (
                            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_./:-]*", cand)
                            and cand not in defined_set
                            and cand not in seen_phantom
                        ):
                            seen_phantom.add(cand)
                            report.broken_references.append(
                                f"Contract drift in {doc.file_path.name}: documented JSON-RPC method '{cand}' in contract '{doc.doc_id}' does not exist in '{spec_file.name}'"
                            )
                    continue

                for m_doc in re.finditer(
                    r"`\s*(?:method|rpc|tool)\s+([A-Za-z_][A-Za-z0-9_./:-]*)\s*`",
                    raw_line,
                    re.IGNORECASE,
                ):
                    clause_before = re.split(r"[.;!?]", raw_line[: m_doc.start()])[-1]
                    if _CONTRAST_CUE_RE.search(clause_before):
                        continue
                    cand = m_doc.group(1).strip()
                    if cand not in defined_set and cand not in seen_phantom:
                        seen_phantom.add(cand)
                        report.broken_references.append(
                            f"Contract drift in {doc.file_path.name}: documented JSON-RPC method '{cand}' in contract '{doc.doc_id}' does not exist in '{spec_file.name}'"
                        )

    def _verify_wsdl_contract(self, doc: SpecDocument, spec_file: Path, report: ValidationReport) -> None:
        verify_wsdl_contract(doc, spec_file, report)

    def _verify_avro_contract(self, doc: SpecDocument, spec_file: Path, report: ValidationReport) -> None:
        verify_avro_contract(doc, spec_file, report)

    def _verify_sql_ddl_contract(self, doc: SpecDocument, spec_file: Path | list[Path], report: ValidationReport) -> None:
        verify_sql_ddl_contract(doc, spec_file, report)

    def _verify_code_interface_contract(
        self, doc: SpecDocument, spec_file: Path | list[Path], report: ValidationReport
    ) -> None:
        verify_code_interface_contract(doc, spec_file, report)

    def _verify_trpc_contract(self, doc: SpecDocument, spec_file: Path | list[Path], report: ValidationReport) -> None:
        verify_trpc_contract(doc, spec_file, report)

    def _verify_websocket_contract(
        self, doc: SpecDocument, spec_file: Path | list[Path], report: ValidationReport
    ) -> None:
        verify_websocket_contract(doc, spec_file, report, parse_doc_auth=self._parse_doc_auth_expectation)

    def _verify_mcp_contract(self, doc: SpecDocument, spec_file: Path | list[Path], report: ValidationReport) -> None:
        verify_mcp_contract(doc, spec_file, report)

    _CONTRACT_PROTOCOL_VERIFIERS: ClassVar[dict[str, str]] = {
        "cli_args": "_verify_cli_contract_commands",
        "python_protocol": "_verify_python_protocol_contract",
        "openapi": "_verify_rest_openapi_contract",
        "rest_http": "_verify_rest_http_contract",
        "json_schema": "_verify_json_schema_contract",
        "protobuf": "_verify_grpc_proto_contract",
        "graphql": "_verify_graphql_contract",
        "asyncapi": "_verify_asyncapi_contract",
        "json_rpc_stdio": "_verify_json_rpc_stdio_contract",
        "code_interface": "_verify_code_interface_contract",
        "wsdl": "_verify_wsdl_contract",
        "avro": "_verify_avro_contract",
        "sql_ddl": "_verify_sql_ddl_contract",
        "trpc": "_verify_trpc_contract",
        "websocket": "_verify_websocket_contract",
        "mcp": "_verify_mcp_contract",
    }

    def _verify_contract_protocol(
        self,
        protocol: str,
        doc: SpecDocument,
        spec_file: Path | list[Path],
        report: ValidationReport,
    ) -> None:
        """Dispatch contract-to-specification_file verification via _CONTRACT_PROTOCOL_VERIFIERS."""
        method_name = self._CONTRACT_PROTOCOL_VERIFIERS.get(protocol)
        if not method_name:
            return
        if protocol == "cli_args":
            if isinstance(spec_file, Path) and spec_file.is_file() and spec_file.suffix == ".py":
                verify_cli_contract_commands(doc, spec_file, report)
            else:
                verify_polyglot_cli_contract(doc, spec_file, report)
            return
        verifier = getattr(self, method_name, None)
        if callable(verifier):
            verifier(doc, spec_file, report)
