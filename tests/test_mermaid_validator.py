"""Tests for in-process QuickJS Mermaid diagram validation and caching (CMP-MERMAID-VALIDATOR, INV-MERMAID-SPEC-VALIDATION)."""

from __future__ import annotations

import concurrent.futures
import hashlib
from pathlib import Path

import eidos.core.spec.mermaid_validator as mv
from eidos.core.index.storage import (
    _advisory_db_lock,
    ensure_cache_schema,
    load_mermaid_validation_cache,
    open_cache_connection,
    save_mermaid_validation_cache,
)
from eidos.core.spec.mermaid_validator import (
    MERMAID_BUNDLE_SHA256,
    STRUCTURAL_FALLBACK_ENGINE_VERSION,
    MermaidBlock,
    extract_mermaid_blocks,
    get_mermaid_engine_version,
    resolve_adoc_line_offset,
    validate_mermaid_blocks,
    validate_mermaid_diagram,
)
from eidos.core.spec.parser import parse_spec_file
from eidos.core.spec.validator import SpecValidator
from eidos.core.workspace.assets import get_mermaid_bundle_path


def test_mermaid_spec_validation_and_cache_lifecycle(tmp_path: Path, monkeypatch) -> None:
    """Verify INV-MERMAID-SPEC-VALIDATION: pinned mermaid.min.js.gz asset, multi-diagram QuickJS mermaid.parse(),
    exact .adoc line-offset error reporting, warm SHA-256 SQLite cache hits with zero QuickJS VM boot,
    cache invalidation on diagram or engine_version change, concurrent thread safety, contended lock fallback,
    applied/rejected delta skipping, and structural-v1 fallback when quickjs-ng is unavailable."""
    # 1. Verify standalone pinned mermaid.min.js.gz asset resolves via CMP-ASSET-LOCATOR
    bundle_path = get_mermaid_bundle_path()
    assert bundle_path.is_file()
    assert hashlib.sha256(bundle_path.read_bytes()).hexdigest() == MERMAID_BUNDLE_SHA256

    # 2. Extraction edge cases: block attributes, variable-length matched fences, comment skipping,
    #    embedded non-mermaid code block ignoring, empty block & unclosed block handling
    body = "\n".join(
        [
            "= Sample Spec",
            "",
            "// [mermaid]",
            "// ----",
            "// broken --> --> x",
            "// ----",
            "",
            "////",
            "[mermaid]",
            "----",
            "flowchart TD",
            "  BROKEN --> --> COMMENTED",
            "----",
            "////",
            "",
            "[source,asciidoc]",
            "----",
            "[mermaid]",
            "....",
            "not a live mermaid diagram",
            "....",
            "----",
            "",
            '.Architecture Overview',
            '[mermaid, target="c4", format="svg"]',
            "------",
            "C4Context",
            '  Person(u, "User", "Engineer")',
            '  System(s, "Eidos", "Engine")',
            '  Rel(u, s, "uses")',
            "------",
            "",
            "[source, mermaid, linenums]",
            "....",
            "sequenceDiagram",
            "  Alice->>Bob: Hello",
            "....",
            "",
            "[mermaid]",
            "----",
            "   ",
            "----",
        ]
    )
    blocks = extract_mermaid_blocks(body)
    assert len(blocks) == 3
    assert blocks[0].header_kind == "mermaid"
    assert blocks[0].code.startswith("C4Context")
    assert blocks[1].header_kind == "source,mermaid"
    assert blocks[1].code.startswith("sequenceDiagram")
    assert blocks[2].code.strip() == ""
    assert extract_mermaid_blocks("= Plain doc with no diagrams\n") == []

    # Unclosed fence is preserved as an explicit unclosed block error
    unclosed = extract_mermaid_blocks("[mermaid]\n----\nflowchart TD\n  A --> B\n")
    assert len(unclosed) == 1 and unclosed[0].unclosed is True
    unclosed_res, unclosed_rows = validate_mermaid_blocks(unclosed)
    assert unclosed_res[0].is_valid is False
    assert "Unclosed" in unclosed_res[0].error_message
    assert unclosed_rows == []

    # 3. Validate all 6 canonical Mermaid diagram types + invalid syntax via QuickJS mermaid.parse()
    mv._shutdown_quickjs_worker()
    cache_dir = tmp_path / "qjs_cache"
    valid_diagrams = [
        'C4Context\n  Person(u, "User", "desc")\n  System(s, "Sys", "desc")\n  Rel(u, s, "uses")',
        "flowchart TD\n  A[Start] --> B[End]",
        "sequenceDiagram\n  Alice->>Bob: Ping\n  Bob-->>Alice: Pong",
        "stateDiagram-v2\n  [*] --> Idle\n  Idle --> Active: run",
        "classDiagram\n  class Validator {\n    +validate() bool\n  }",
        "erDiagram\n  USER ||--o{ ORDER : places",
    ]
    for diag in valid_diagrams:
        ok, err = validate_mermaid_diagram(diag, cache_dir=cache_dir)
        assert ok is True, f"Expected valid diagram to pass, got error: {err}\n{diag}"
        assert err == ""

    # Verify bytecode cache file was materialized cleanly
    bc_files = list(cache_dir.glob("mermaid_parser.*.qjsbc"))
    assert len(bc_files) == 1

    # Corrupt bytecode cache self-heals on fresh worker boot
    bc_files[0].write_bytes(b"corrupt-qjs-bytecode")
    mv._shutdown_quickjs_worker()
    ok_heal, err_heal = validate_mermaid_diagram("flowchart LR\n  X --> Y", cache_dir=cache_dir)
    assert ok_heal is True and err_heal == ""

    # Invalid diagram syntax rejected with real Mermaid parser error
    ok_bad, err_bad = validate_mermaid_diagram("flowchart TD\n  A --> --> B", cache_dir=cache_dir)
    assert ok_bad is False
    assert "Parse error" in err_bad or "Expecting" in err_bad

    # Empty diagram rejected
    ok_empty, err_empty = validate_mermaid_diagram("   ")
    assert ok_empty is False and "empty" in err_empty.lower()

    # 4. Concurrent multi-thread safety against single QuickJS worker
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futs = [pool.submit(validate_mermaid_diagram, d, cache_dir=cache_dir) for d in valid_diagrams * 2]
        assert all(f.result()[0] is True for f in futs)

    # 5. End-to-end SpecValidator directory validation + warm SHA-256 SQLite cache hit (zero QuickJS boot)
    repo = tmp_path / "mermaid_repo"
    eidos_dir = repo / "eidos"
    (eidos_dir / "deltas").mkdir(parents=True)
    db_path = eidos_dir / ".cache" / "index.db"
    conn = open_cache_connection(db_path)
    ensure_cache_schema(conn, db_path=db_path)
    conn.close()

    sys_spec = eidos_dir / "system.adoc"
    sys_spec.write_text(
        "---\n"
        "type: product/system\n"
        "id: SYS-MERM-001\n"
        "title: Mermaid Test System\n"
        "status: active\n"
        "archetypes: [cli]\n"
        "---\n"
        "= Mermaid Test System\n\n"
        "[mermaid]\n"
        "----\n"
        "flowchart TD\n"
        "  A[Alpha] --> B[Beta]\n"
        "----\n",
        encoding="utf-8",
    )
    # Historical applied and rejected SpecDeltas with broken Mermaid must be skipped
    (eidos_dir / "deltas" / "dlt-2026-001-applied.adoc").write_text(
        "---\n"
        "type: delta/mutation\n"
        "id: DLT-2026-001-APPLIED\n"
        "title: Applied Delta\n"
        "status: applied\n"
        "intent:\n"
        "  kind: feature\n"
        "  motivation: Historical applied delta\n"
        "mutations:\n"
        "  - operation: modify\n"
        "    target: SYS-MERM-001\n"
        "    details: Historical\n"
        "---\n"
        "= Applied Delta\n\n"
        "[mermaid]\n"
        "----\n"
        "flowchart TD\n"
        "  BROKEN --> --> HIST\n"
        "----\n",
        encoding="utf-8",
    )
    (eidos_dir / "deltas" / "dlt-2026-002-rejected.adoc").write_text(
        "---\n"
        "type: delta/mutation\n"
        "id: DLT-2026-002-REJECTED\n"
        "title: Rejected Delta\n"
        "status: rejected\n"
        "intent:\n"
        "  kind: feature\n"
        "  motivation: Historical rejected delta\n"
        "mutations:\n"
        "  - operation: modify\n"
        "    target: SYS-MERM-001\n"
        "    details: Historical\n"
        "---\n"
        "= Rejected Delta\n\n"
        "[mermaid]\n"
        "----\n"
        "flowchart TD\n"
        "  BROKEN --> --> REJ\n"
        "----\n",
        encoding="utf-8",
    )

    validator = SpecValidator()
    rep1 = validator.validate_directory(eidos_dir, check_import_boundaries=False)
    assert rep1.is_clean is True, rep1.schema_errors

    # Verify cache hydrated in mermaid_validation_cache
    eng_ver = get_mermaid_engine_version()
    cached_map = load_mermaid_validation_cache(db_path, eng_ver)
    assert len(cached_map) == 1

    # Warm second pass MUST hit cache with zero calls to validate_mermaid_diagram / QuickJS VM
    def _forbid_vm(*args, **kwargs):
        raise AssertionError("Warm mermaid_validation_cache hit must not invoke QuickJS VM!")

    with monkeypatch.context() as mp:
        mp.setattr(mv, "validate_mermaid_diagram", _forbid_vm)
        rep2 = validator.validate_directory(eidos_dir, check_import_boundaries=False)
        assert rep2.is_clean is True

    # Engine-version mismatch invalidates cache lookup
    assert load_mermaid_validation_cache(db_path, "different-engine-v0") == {}

    # Non-blocking save under contended index.db.lock skips cleanly without raising
    with _advisory_db_lock(db_path, non_blocking=False):
        # Use a separate thread to simulate contention on the non-blocking write path
        def _contended_save() -> None:
            save_mermaid_validation_cache(db_path, [("deadbeef", eng_ver, 1, "")])

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(_contended_save).result(timeout=5)
    assert "deadbeef" not in load_mermaid_validation_cache(db_path, eng_ver)

    # 6. Broken Mermaid block reports blocking schema_error with exact 1-based .adoc file line number
    broken_content = (
        "---\n"  # line 1
        "type: product/system\n"  # line 2
        "id: SYS-MERM-001\n"  # line 3
        "title: Mermaid Test System\n"  # line 4
        "status: active\n"  # line 5
        "archetypes: [cli]\n"  # line 6
        "---\n"  # line 7
        "= Mermaid Test System\n"  # line 8
        "\n"  # line 9
        "[mermaid]\n"  # line 10
        "----\n"  # line 11
        "flowchart TD\n"  # line 12 -> diagram content starts here!
        "  A --> --> B\n"  # line 13
        "----\n"  # line 14
    )
    sys_spec.write_text(broken_content, encoding="utf-8")

    # Single-doc validation with check_mermaid=False skips Mermaid; default check_mermaid=True catches it
    parsed_doc = parse_spec_file(sys_spec)
    errs_no_mermaid = validator.validate_document(parsed_doc, root_dir=eidos_dir, check_mermaid=False)
    assert errs_no_mermaid == []

    parsed_doc2 = parse_spec_file(sys_spec)
    errs_with_mermaid = validator.validate_document(parsed_doc2, root_dir=eidos_dir, check_mermaid=True)
    assert len(errs_with_mermaid) == 1
    assert "Mermaid syntax error in [mermaid] block at line 12:" in errs_with_mermaid[0]

    # Directory validation also surfaces blocking schema_error with repo-relative path and exact line 12
    rep_bad = validator.validate_directory(eidos_dir, check_import_boundaries=False)
    assert rep_bad.is_clean is False
    assert "eidos/system.adoc" in rep_bad.schema_errors
    assert any("at line 12:" in e for e in rep_bad.schema_errors["eidos/system.adoc"])

    # Empty blocks and empty block list in validate_mermaid_blocks
    assert validate_mermaid_blocks([]) == ([], [])
    empty_res, empty_rows = validate_mermaid_blocks([MermaidBlock(code="   ", body_line=1)])
    assert empty_res[0].is_valid is False and "empty" in empty_res[0].error_message.lower()
    assert empty_rows == []

    # resolve_adoc_line_offset fallbacks for None / missing / frontmatter-less files
    assert resolve_adoc_line_offset(None, 5) == 5
    assert resolve_adoc_line_offset(tmp_path / "missing.adoc", 7) == 7
    plain_adoc = tmp_path / "plain.adoc"
    plain_adoc.write_text("= Plain\n\nBody\n", encoding="utf-8")
    assert resolve_adoc_line_offset(plain_adoc, 3) == 3

    # 7. Structural fallback ('structural-v1') when quickjs-ng is unavailable
    with monkeypatch.context() as mp:
        mp.setattr(mv, "_is_quickjs_available", lambda: False)
        assert get_mermaid_engine_version() == STRUCTURAL_FALLBACK_ENGINE_VERSION

        assert validate_mermaid_diagram("flowchart TD\n  subgraph S\n    A --> B\n  end")[0] is True
        assert validate_mermaid_diagram("sequenceDiagram\n  alt ok\n    A->>B: hi\n  end")[0] is True
        assert validate_mermaid_diagram('%%{"theme": "dark"}%%\nflowchart LR\n  A["ok"] --> B')[0] is True

        ok_hdr, err_hdr = validate_mermaid_diagram("notADiagramType\n  A --> B")
        assert ok_hdr is False and "Unrecognized Mermaid diagram header" in err_hdr

        ok_dir, err_dir = validate_mermaid_diagram("%%{bad json}%%\nflowchart TD\n  A --> B")
        assert ok_dir is False and "directive JSON" in err_dir

        ok_q, err_q = validate_mermaid_diagram('flowchart TD\n  A["unclosed] --> B')
        assert ok_q is False and "Unclosed double quote" in err_q

        ok_sub, err_sub = validate_mermaid_diagram("flowchart TD\n  subgraph S\n    A --> B")
        assert ok_sub is False and "Unclosed Mermaid block" in err_sub

        ok_end, err_end = validate_mermaid_diagram("flowchart TD\n  end")
        assert ok_end is False and "Unexpected 'end'" in err_end

        # Direct validate_mermaid_blocks with db_path manages cache automatically & deduplicates identical blocks
        dup_blk = MermaidBlock(code="flowchart LR\n  P --> Q", body_line=2)
        res_list, new_rows = validate_mermaid_blocks([dup_blk, dup_blk], db_path=db_path)
        assert len(res_list) == 2 and all(r.is_valid for r in res_list)
        assert len(new_rows) == 1 and new_rows[0][1] == STRUCTURAL_FALLBACK_ENGINE_VERSION
        res_cached, new_rows2 = validate_mermaid_blocks([dup_blk], db_path=db_path)
        assert res_cached[0].from_cache is True and new_rows2 == []
