"""Multi-protocol bidirectional CTR-* interface contract parity validator (CMP-CONTRACT-PARITY-VALIDATOR)."""

import ast
import re
from pathlib import Path
from typing import ClassVar

from eidos.core.spec.cli_contract_parity import _parse_python_ast, verify_cli_contract_commands
from eidos.core.spec.models import SpecDocument, ValidationReport
from eidos.core.spec.parser import YamlError, safe_load_yaml


class ContractParityValidator:
    """Verifies bidirectional parity between CTR-* contract specs and specification_file targets across 6 protocols."""

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
        """Verify bidirectional parity between OpenAPI specification_file paths and documented routes in the contract (DLT-2026-082)."""
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
        if not isinstance(paths, dict):
            return
        body = self._strip_asciidoc_comments(doc.body or "")
        defined_routes = {str(k) for k in paths}
        for route in sorted(defined_routes):
            if not re.search(rf"(?<![A-Za-z0-9_./:~-]){re.escape(route)}(?=$|[\s`\"'|),;\]>])", body):
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: OpenAPI path '{route}' defined in '{spec_file.name}' is not documented in contract '{doc.doc_id}'"
                )

        http_verbs = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
        stripped_for_rev = re.sub(
            r"(?ms)^(?:----|```)[^\r\n]*\r?\n.*?^(?:----|```)\s*$",
            "",
            body,
        )
        non_iface_heading_re = re.compile(
            r"\b(?:architectural\s+boundary|boundary|context|background|non[- ]goals?|out[- ]of[- ]scope|exclusions?|excluded|notes?|operational\s+notes|design\s+constraints|quality\s+attributes|migration|deprecated|history|rationale|alternatives?)\b",
            re.IGNORECASE,
        )
        contrast_cue_re = re.compile(
            r"\b(?:unlike|out[- ]of[- ]scope|exclud(?:ed|es|ing)|instead\s+of|rather\s+than|does\s+not\s+expose|not\s+exposed|do\s+not\s+(?:call|expose|use)|not\s+part\s+of|handled\s+by|owned\s+by|belongs?\s+to|delegated\s+to|legacy|deprecated)\b",
            re.IGNORECASE,
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
                if len(h2_matches) > 1 and non_iface_heading_re.search(heading_txt):
                    continue
                sections_to_scan.append(stripped_for_rev[hm.end() : sec_end])

        doc_route_candidates: list[str] = []
        for sec_chunk in sections_to_scan:
            for raw_line in sec_chunk.splitlines():
                stripped_line = raw_line.strip()
                if stripped_line.startswith("|") and not stripped_line.startswith("|==="):
                    cells = [c.strip().strip("`*") for c in stripped_line.split("|")[1:]]
                    if any(c.upper() in http_verbs for c in cells):
                        for cell in cells:
                            if re.fullmatch(r"/[A-Za-z0-9._~/{}:-]*", cell):
                                doc_route_candidates.append(cell)
                for m in re.finditer(
                    r"`\s*(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/[A-Za-z0-9._~/{}:-]*)(?:\s[^`\r\n]*)?`",
                    raw_line,
                ):
                    if not stripped_line.startswith("|"):
                        clause_before = re.split(r"[.;!?]", raw_line[: m.start()])[-1]
                        if contrast_cue_re.search(clause_before):
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

    def _verify_json_schema_contract(self, doc: SpecDocument, spec_file: Path, report: ValidationReport) -> None:
        """Verify that JSON Schema specification_file is valid YAML/JSON and its required properties are documented in the contract."""
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
        if not isinstance(props, dict) or not props:
            return
        raw_req = data.get("required")
        req_source = raw_req if isinstance(raw_req, list) else props.keys()
        required_props = list(dict.fromkeys(str(p) for p in req_source if str(p) in props))
        body = self._strip_asciidoc_comments(doc.body or "")
        for prop_name in required_props:
            if not re.search(rf"(?:`|\b){re.escape(prop_name)}\b", body):
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: JSON Schema property '{prop_name}' defined in '{spec_file.name}' is not documented in contract '{doc.doc_id}'"
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
        protocol = str(doc.model.protocol or "").strip().lower()
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

        if protocol == "protobuf":
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

    _CONTRACT_PROTOCOL_VERIFIERS: ClassVar[dict[str, str]] = {
        "cli_args": "_verify_cli_contract_commands",
        "python_protocol": "_verify_python_protocol_contract",
        "rest_openapi": "_verify_rest_openapi_contract",
        "openapi": "_verify_rest_openapi_contract",
        "json_schema": "_verify_json_schema_contract",
        "grpc_proto": "_verify_grpc_proto_contract",
        "protobuf": "_verify_grpc_proto_contract",
        "graphql": "_verify_graphql_contract",
    }

    def _verify_contract_protocol(
        self, protocol: str, doc: SpecDocument, spec_file: Path, report: ValidationReport
    ) -> None:
        """Dispatch contract-to-specification_file verification via _CONTRACT_PROTOCOL_VERIFIERS."""
        method_name = self._CONTRACT_PROTOCOL_VERIFIERS.get(protocol)
        if not method_name:
            return
        if protocol == "cli_args" and spec_file.suffix != ".py":
            return
        verifier = getattr(self, method_name, None)
        if callable(verifier):
            verifier(doc, spec_file, report)
