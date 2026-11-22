"""Schema & IDL contract parity verifiers (wsdl, avro, sql_ddl) plus shared parity utilities (CMP-CONTRACT-PARITY-VALIDATOR)."""

from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from eidos.core.spec.models import SpecDocument, ValidationReport
from eidos.core.workspace.project import STANDARD_EXCLUDED_DIRS

PROTOCOL_SKIP_DIRS: frozenset[str] = STANDARD_EXCLUDED_DIRS | {"bin", "obj"}
_TEST_DIR_NAMES: frozenset[str] = frozenset({"tests", "test", "__tests__", "spec", "specs"})
_TEST_FILE_RE = re.compile(r"(?:^test_.*|_test\.[^.]+|\.(?:test|spec)\.[^.]+|Tests\.cs)$", re.IGNORECASE)

NON_IFACE_HEADING_RE = re.compile(
    r"\b(?:architectural\s+boundary|boundary|context|background|non[- ]goals?|out[- ]of[- ]scope|exclusions?|excluded|notes?|operational\s+notes|design\s+constraints|quality\s+attributes|migration|deprecated|history|rationale|alternatives?)\b",
    re.IGNORECASE,
)
CONTRAST_CUE_RE = re.compile(
    r"\b(?:unlike|out[- ]of[- ]scope|exclud(?:ed|es|ing)|instead\s+of|rather\s+than|does\s+not\s+expose|not\s+exposed|do\s+not\s+(?:call|expose|use)|not\s+part\s+of|handled\s+by|owned\s+by|belongs?\s+to|delegated\s+to|legacy|deprecated)\b",
    re.IGNORECASE,
)

_MAX_WSDL_BYTES = 5 * 1024 * 1024
_XML_UNSAFE_DTD_RE = re.compile(r"<!(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
_SQL_DDL_RE = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:(?:TEMP|TEMPORARY|MATERIALIZED)\s+)?(TABLE|VIEW|FUNCTION|PROCEDURE)\s+(?:IF\s+NOT\s+EXISTS\s+)?([`\"\[\]A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)
_PRISMA_DECL_RE = re.compile(r"^\s*(model|enum|view)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{", re.MULTILINE)
_AVDL_TYPE_RE = re.compile(r"\b(protocol|record|enum|fixed|error)\s+([A-Za-z_][A-Za-z0-9_]*)\b")
_AVDL_MESSAGE_RE = re.compile(
    r"^\s*(?:void|[A-Za-z_][A-Za-z0-9_<>.,?\s\[\]]+?)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\([^)]*\)\s*(?:throws\s+[^;]+)?;",
    re.MULTILINE,
)


def strip_asciidoc_comments(body: str) -> str:
    """Strip AsciiDoc block comments (//// ... ////) and single-line comments (// ...) from contract body."""
    cleaned = re.sub(
        r"^[ \t]*/{4,}[ \t]*\r?\n(?:.*?\r?\n)?[ \t]*/{4,}[ \t]*$",
        "",
        body,
        flags=re.MULTILINE | re.DOTALL,
    )
    return re.sub(r"^[ \t]*//[^\r\n]*$", "", cleaned, flags=re.MULTILINE)


def iter_contract_interface_sections(body: str) -> list[str]:
    """Return body sections eligible for reverse phantom-entry checks (excluding boundary/non-goal/out-of-scope sections)."""
    heading_matches = list(re.finditer(r"(?m)^(==+)\s+(.+)$", body))
    if not heading_matches:
        return [body]
    sections: list[str] = []
    for idx, hm in enumerate(heading_matches):
        if NON_IFACE_HEADING_RE.search(hm.group(2)):
            continue
        start = hm.end()
        end = heading_matches[idx + 1].start() if idx + 1 < len(heading_matches) else len(body)
        sections.append(body[start:end])
    return sections


def collect_protocol_source_files(
    spec_file: Path | list[Path],
    allowed_exts: frozenset[str],
    *,
    skip_tests: bool = True,
) -> list[Path]:
    """Safely expand a file, directory, or list of Paths into concrete files matching ``allowed_exts``."""
    raw_targets = [spec_file] if isinstance(spec_file, Path) else list(spec_file)
    files: list[Path] = []
    seen: set[Path] = set()

    def _matches_ext(path: Path) -> bool:
        lower_name = path.name.lower()
        return any(lower_name.endswith(ext) for ext in allowed_exts)

    for target in raw_targets:
        if target.is_file():
            if _matches_ext(target) and target not in seen:
                seen.add(target)
                files.append(target)
        elif target.is_dir():
            for root_s, dirnames, filenames in os.walk(target):
                dirnames[:] = sorted(
                    d
                    for d in dirnames
                    if d not in PROTOCOL_SKIP_DIRS
                    and not d.startswith(".")
                    and (not skip_tests or d.lower() not in _TEST_DIR_NAMES)
                )
                for fn in sorted(filenames):
                    if skip_tests and _TEST_FILE_RE.search(fn):
                        continue
                    cand = Path(root_s) / fn
                    if _matches_ext(cand) and cand not in seen:
                        seen.add(cand)
                        files.append(cand)
    return files


def format_spec_target_label(spec_file: Path | list[Path]) -> str:
    """Return a concise human-readable label for a single or multi-file contract target."""
    if isinstance(spec_file, Path):
        return spec_file.name
    return spec_file[0].name if len(spec_file) == 1 else f"{len(spec_file)} files"


def check_forward_and_reverse_symbols(
    doc: SpecDocument,
    target_label: str,
    body: str,
    forward_symbols: list[str],
    *,
    allowed_symbols: set[str] | None = None,
    entity_label: str,
    table_header_re: str,
    backtick_prefixes: tuple[str, ...],
    token_pattern: str = r"[A-Za-z_][A-Za-z0-9_.:/-]*",
    case_insensitive: bool = False,
    report: ValidationReport,
) -> None:
    """Verify forward presence of ``forward_symbols`` and reverse phantom drift across interface tables and backticked tokens."""
    search_flags = re.IGNORECASE if case_insensitive else 0
    for sym in forward_symbols:
        if not re.search(rf"(?<![A-Za-z0-9_./:-]){re.escape(sym)}(?![A-Za-z0-9_./:-])", body, flags=search_flags):
            report.broken_references.append(
                f"Contract drift in {doc.file_path.name}: {entity_label} '{sym}' defined in '{target_label}' "
                f"is not documented in contract '{doc.doc_id}'"
            )

    valid_set = set(allowed_symbols if allowed_symbols is not None else forward_symbols)
    valid_lookup = {s.lower() for s in valid_set} if case_insensitive else valid_set

    prefix_alt = "|".join(re.escape(p) for p in backtick_prefixes)
    backtick_re = re.compile(rf"`\s*(?:{prefix_alt})\s+({token_pattern})(?:\s*\([^`]*\))?\s*`", re.IGNORECASE)
    cell_re = re.compile(rf"^{token_pattern}$")
    header_re = re.compile(table_header_re, re.IGNORECASE)

    seen_phantom: set[str] = set()

    def _record_if_phantom(candidate: str) -> None:
        cand = candidate.strip()
        key = cand.lower() if case_insensitive else cand
        if not cand or key in valid_lookup or key in seen_phantom:
            return
        seen_phantom.add(key)
        report.broken_references.append(
            f"Contract drift in {doc.file_path.name}: documented {entity_label} '{cand}' in contract '{doc.doc_id}' "
            f"does not exist in '{target_label}'"
        )

    for sec_chunk in iter_contract_interface_sections(body):
        in_table = False
        target_col_idx: int | None = None
        header_seen = False
        for raw_line in sec_chunk.splitlines():
            stripped = raw_line.strip()
            if stripped.startswith("|==="):
                in_table = not in_table
                target_col_idx = None
                header_seen = False
                continue
            if stripped.startswith("|"):
                cells = [c.strip().strip("`*") for c in stripped.split("|")[1:]]
                if in_table and not header_seen:
                    header_seen = True
                    for idx, c in enumerate(cells):
                        if header_re.search(c):
                            target_col_idx = idx
                            break
                    continue
                if target_col_idx is not None and target_col_idx < len(cells):
                    raw_cell = cells[target_col_idx].strip()
                    cand = raw_cell.split("(", 1)[0].strip()
                    if cell_re.fullmatch(cand):
                        _record_if_phantom(cand)
                continue

            for m_doc in backtick_re.finditer(raw_line):
                clause_before = re.split(r"[.;!?]", raw_line[: m_doc.start()])[-1]
                if CONTRAST_CUE_RE.search(clause_before):
                    continue
                _record_if_phantom(m_doc.group(1))


def verify_wsdl_contract(doc: SpecDocument, spec_file: Path, report: ValidationReport) -> None:
    """Verify bidirectional parity between a WSDL 1.1/2.0 XML file and the contract."""
    try:
        if spec_file.stat().st_size > _MAX_WSDL_BYTES:
            report.broken_references.append(
                f"Invalid WSDL specification_file in {doc.file_path.name}: '{spec_file.name}' exceeds 5 MiB limit"
            )
            return
        raw_xml = spec_file.read_text(encoding="utf-8")
    except OSError:
        return

    if _XML_UNSAFE_DTD_RE.search(raw_xml):
        report.broken_references.append(
            f"Invalid WSDL specification_file in {doc.file_path.name}: '{spec_file.name}' contains disallowed XML DTD/ENTITY declaration"
        )
        return

    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError as exc:
        report.broken_references.append(
            f"Invalid WSDL specification_file in {doc.file_path.name}: '{spec_file.name}' failed to parse ({exc})"
        )
        return

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    services: list[str] = []
    operations: list[str] = []
    for elem in root.iter():
        lname = _local(elem.tag)
        if lname == "service":
            name = (elem.attrib.get("name") or "").strip()
            if name:
                services.append(name)
        elif lname in ("portType", "interface"):
            for child in elem:
                if _local(child.tag) == "operation":
                    op_name = (child.attrib.get("name") or "").strip()
                    if op_name:
                        operations.append(op_name)

    defined = list(dict.fromkeys([*services, *operations]))
    if not defined:
        return

    body = strip_asciidoc_comments(doc.body or "")
    check_forward_and_reverse_symbols(
        doc,
        spec_file.name,
        body,
        defined,
        entity_label="WSDL symbol",
        table_header_re=r"\b(?:operation|service)\b",
        backtick_prefixes=("operation", "service"),
        report=report,
    )


def _collect_avro_json_symbols(node: object, types_out: list[str], fields_out: list[str]) -> None:
    if isinstance(node, list):
        for item in node:
            _collect_avro_json_symbols(item, types_out, fields_out)
        return
    if not isinstance(node, dict):
        return
    avro_type = node.get("type")
    name = node.get("name")
    if avro_type in ("record", "enum", "fixed", "error") and isinstance(name, str) and name.strip():
        types_out.append(name.strip())
    raw_fields = node.get("fields")
    if isinstance(raw_fields, list):
        for field in raw_fields:
            if isinstance(field, dict):
                fname = field.get("name")
                if isinstance(fname, str) and fname.strip():
                    fields_out.append(fname.strip())
                _collect_avro_json_symbols(field.get("type"), types_out, fields_out)
    if isinstance(avro_type, (dict, list)):
        _collect_avro_json_symbols(avro_type, types_out, fields_out)
    for nested_key in ("items", "values"):
        if nested_key in node:
            _collect_avro_json_symbols(node[nested_key], types_out, fields_out)


def verify_avro_contract(doc: SpecDocument, spec_file: Path, report: ValidationReport) -> None:
    """Verify bidirectional parity for Avro .avsc (JSON) and .avdl (IDL) schemas."""
    suffix = spec_file.suffix.lower()
    try:
        text = spec_file.read_text(encoding="utf-8")
    except OSError:
        return

    defined_types: list[str] = []
    defined_members: list[str] = []

    if suffix == ".avsc":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            report.broken_references.append(
                f"Invalid Avro specification_file in {doc.file_path.name}: '{spec_file.name}' failed to parse ({exc})"
            )
            return
        _collect_avro_json_symbols(data, defined_types, defined_members)
    elif suffix == ".avdl":
        stripped = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        stripped = re.sub(r"//[^\r\n]*", "", stripped)
        for m in _AVDL_TYPE_RE.finditer(stripped):
            defined_types.append(m.group(2))
        for m_msg in _AVDL_MESSAGE_RE.finditer(stripped):
            msg_name = m_msg.group(1)
            if msg_name not in ("import", "schema", "protocol", "record", "enum", "fixed", "error"):
                defined_members.append(msg_name)
    else:
        return

    forward = list(dict.fromkeys([*defined_types, *defined_members]))
    if not forward:
        return

    body = strip_asciidoc_comments(doc.body or "")
    check_forward_and_reverse_symbols(
        doc,
        spec_file.name,
        body,
        forward,
        entity_label="Avro symbol",
        table_header_re=r"\b(?:record|enum|fixed|error|message|field|type|symbol)\b",
        backtick_prefixes=("record", "enum", "fixed", "error", "message"),
        report=report,
    )


def verify_sql_ddl_contract(doc: SpecDocument, spec_file: Path | list[Path], report: ValidationReport) -> None:
    """Verify bidirectional parity for SQL DDL (.sql) and Prisma (.prisma) database contracts."""
    source_files = collect_protocol_source_files(spec_file, frozenset({".sql", ".prisma"}))
    if not source_files:
        return

    forward_objects: list[str] = []
    allowed_objects: set[str] = set()

    for path in source_files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if path.suffix.lower() == ".prisma":
            cleaned = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
            cleaned = re.sub(r"//[^\r\n]*", "", cleaned)
            for m in _PRISMA_DECL_RE.finditer(cleaned):
                obj_name = m.group(2)
                forward_objects.append(obj_name)
                allowed_objects.add(obj_name)
        else:
            cleaned = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
            cleaned = re.sub(r"--[^\r\n]*", "", cleaned)
            for m in _SQL_DDL_RE.finditer(cleaned):
                raw_name = m.group(2).strip().rstrip("(")
                parts = [p.strip('`"[]') for p in raw_name.split(".") if p.strip('`"[]')]
                if not parts:
                    continue
                base_name = parts[-1]
                full_name = ".".join(parts)
                forward_objects.append(base_name)
                allowed_objects.add(base_name)
                allowed_objects.add(full_name)

    forward_objects = list(dict.fromkeys(forward_objects))
    if not forward_objects:
        return

    body = strip_asciidoc_comments(doc.body or "")
    check_forward_and_reverse_symbols(
        doc,
        format_spec_target_label(spec_file),
        body,
        forward_objects,
        allowed_symbols=allowed_objects,
        entity_label="SQL/DDL object",
        table_header_re=r"\b(?:table|view|function|procedure|model|enum|object)\b",
        backtick_prefixes=("table", "view", "function", "procedure", "model", "enum"),
        case_insensitive=True,
        report=report,
    )
