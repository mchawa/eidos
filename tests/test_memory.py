"""Tests for CMP-MEMORY-ENGINE and CMP-CLI-MEMORY-COMMANDS (INV-MEMORY-LIFECYCLE-AND-SANITIZER, INV-MEMORY-CLI-AND-SKILL-PARITY)."""

from __future__ import annotations

import subprocess
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import orjson
import pytest
from typer.testing import CliRunner

from eidos.cli.main import app
from eidos.core.index.graph_index import get_architectural_index
from eidos.core.index.semantic import SEMANTIC_SYNC_COMPLETE_KEY, pack_embedding_f32
from eidos.core.lifecycle.initializer import init_repository
from eidos.core.lifecycle.memory_engine import (
    add_memory,
    archive_memory,
    audit_memories,
    detect_memory_overlaps,
    find_memory_by_id_or_path,
    generate_memory_id,
    list_memories,
    promote_memory,
    resolve_promotion_target,
    sanitize_memory_asciidoc_body,
    search_memories,
    show_memory,
    verify_memory,
)
from eidos.core.spec.parser import parse_spec_file
from eidos.core.spec.validator import SpecValidator


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def _setup_memory_repo(tmp_path: Path, name: str = "mem_cli_repo") -> tuple[Path, Path, str]:
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@eidos.dev")
    _git(repo, "config", "user.name", "Test")

    init_repository(repo, title="Team Memory Test Repo")
    eidos_dir = repo / "eidos"
    (eidos_dir / "domains" / "core" / "capabilities").mkdir(parents=True, exist_ok=True)
    (eidos_dir / "architecture" / "containers").mkdir(parents=True, exist_ok=True)
    (eidos_dir / "architecture" / "components").mkdir(parents=True, exist_ok=True)
    (eidos_dir / "policies").mkdir(parents=True, exist_ok=True)

    (eidos_dir / "domains" / "core" / "domain.adoc").write_text(
        "---\ntype: product/domain\nid: DOM-CORE\ntitle: Core Domain\nstatus: active\n---\n= Core Domain\n",
        encoding="utf-8",
    )
    (eidos_dir / "policies" / "pol-zero-ai.adoc").write_text(
        "---\n"
        "type: product/policy\n"
        "id: POL-CORE-ZERO-AI\n"
        'title: "Zero Remote AI API Dependency"\n'
        "status: active\n"
        "category: governance\n"
        "severity: blocking\n"
        "invariants:\n"
        "  - id: INV-FIXTURE-ZERO-AI\n"  # eidos:fixture
        '    rule: "Never invoke remote OpenAI Anthropic or Gemini LLM APIs from deterministic offline CLI commands."\n'
        "---\n= Zero Remote AI Policy\n",
        encoding="utf-8",
    )
    (eidos_dir / "architecture" / "containers" / "cnt-core.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-CORE\n"
        "title: Core Container\n"
        "status: active\n"
        "domain: DOM-CORE\n"
        "container_type: library\n"
        "runtime: Python 3.12\n"
        "source_path: src/core\n"
        "decisions:\n"
        "  - id: DEC-CORE-SQLITE-REAL\n"
        '    title: "Use Real SQLite DBs In Tests"\n'
        '    decision: "Always construct real temporary SQLite databases via tmp_path in storage tests."\n'
        '    rationale: "Mocked cursors hide FTS5 trigger regressions."\n'
        "---\n= Core Container\n",
        encoding="utf-8",
    )
    (eidos_dir / "architecture" / "components" / "cmp-db.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-CORE-DB\n"
        "title: CoreStorage\n"
        "container: CNT-CORE\n"
        "status: active\n"
        "source_path: src/core/db.py\n"
        "---\n= CoreStorage\n",
        encoding="utf-8",
    )
    (eidos_dir / "domains" / "core" / "capabilities" / "cap-core.adoc").write_text(
        "---\n"
        "type: product/capability\n"
        "id: CAP-CORE-001\n"
        "title: Core Storage Reliability\n"
        "domain: DOM-CORE\n"
        "status: active\n"
        "technical:\n"
        "  containers: [CNT-CORE]\n"
        "  components: [CMP-CORE-DB]\n"
        "  invariants:\n"
        "    - id: INV-FIXTURE-DB-WAL\n"  # eidos:fixture
        '      rule: "SQLite storage tests must use real tmp_path WAL databases rather than mocked cursors."\n'
        "---\n= Core Storage Reliability\n",
        encoding="utf-8",
    )
    (repo / "src" / "core").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "core" / "db.py").write_text("def open_db():\n    return True\n", encoding="utf-8")

    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "initial")
    head_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    return repo, eidos_dir, head_sha


def test_memory_lifecycle_sanitizer_overlap_and_promotion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify INV-MEMORY-LIFECYCLE-AND-SANITIZER: collision-resistant IDs, zero-warning AsciiDoc sanitizer, calibrated BM25 & pre-computed Harrier overlap detection, freshness verification, archiving, and standalone/embedded promotion."""
    # 1. Collision-resistant ID generation: word-boundary trimming, empty fallback, on-disk re-roll
    mid_long, fn_long = generate_memory_id(
        "Never mock SQLite database cursors in integration tests for FTS5 indexing engines"
    )
    assert mid_long.startswith("MEM-")
    assert fn_long.startswith("mem-") and fn_long.endswith(".adoc")
    assert len(fn_long.removeprefix("mem-").removesuffix(".adoc")) <= 36 + 5
    mid_empty, _ = generate_memory_id("!!!   ---")
    assert mid_empty.startswith("MEM-NOTE-")

    # 2. Zero-warning AsciiDoc body sanitizer on adversarial agent inputs
    raw_adv = (
        "## Do Not Mock SQLite\r\n"
        "==== Skipped Level 4\r\n"
        "Before\r\n---\r\nAfter\r\n"
        "include::/etc/passwd[]\r\n"
        "Items:\r\n"
        "- Top item\r\n"
        "\t\t- Deep jump clamped\r\n"
        "```python\r\n"
        "def test_ok(tmp_path):\r\n"
        "    assert True\r\n"
        "```\r\n"
        "Unclosed:\r\n"
        "```\r\n"
        "plain block\r\n"
    )
    san = sanitize_memory_asciidoc_body(raw_adv)
    assert "*Do Not Mock SQLite*" in san
    assert "*Skipped Level 4*" in san
    assert "'''" in san
    assert "`include::/etc/passwd[]`" in san
    assert "* Top item" in san
    assert "** Deep jump clamped" in san
    assert "[source,python]\n----" in san
    assert "[source,text]\n----" in san
    assert san.count("----") == 4  # closed both fenced blocks cleanly
    assert "##" not in san and "====" not in san

    # Native AsciiDoc listing block (----) is preserved intact instead of rewritten to '''
    native_adoc = sanitize_memory_asciidoc_body("[source,bash]\n----\nuv run pytest\n----\n")
    assert "[source,bash]\n----\nuv run pytest\n----" in native_adoc
    assert "'''" not in native_adoc

    # 3. Hermetic repository lifecycle
    repo, eidos_dir, head_sha = _setup_memory_repo(tmp_path)
    today = date(2026, 11, 25)

    # Reject invalid inputs cleanly
    with pytest.raises(ValueError, match="title"):
        add_memory(eidos_dir, title="   ", category="feedback", summary="s", use_semantic=False)
    with pytest.raises(ValueError, match="client-native memory"):
        add_memory(eidos_dir, title="Prefer concise replies", category="user", summary="s", use_semantic=False)
    with pytest.raises(ValueError, match="Invalid category"):
        add_memory(eidos_dir, title="Bad cat", category="bogus", summary="s", use_semantic=False)
    with pytest.raises(ValueError, match="summary cannot be empty"):
        add_memory(eidos_dir, title="No sum", category="feedback", summary="   ", use_semantic=False)
    with pytest.raises(ValueError, match="exceeds 220"):
        add_memory(eidos_dir, title="Long sum", category="feedback", summary="x" * 221, use_semantic=False)
    with pytest.raises(ValueError, match="Invalid initial status"):
        add_memory(eidos_dir, title="Bad st", category="feedback", summary="ok", status="archived", use_semantic=False)
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        add_memory(
            eidos_dir, title="Bad exp", category="project", summary="ok", expires_at="11/25/2026", use_semantic=False
        )
    with pytest.raises(ValueError, match="cannot be empty"):
        find_memory_by_id_or_path(eidos_dir, "   ")
    with pytest.raises(ValueError, match="not found"):
        find_memory_by_id_or_path(eidos_dir, "MEM-DOES-NOT-EXIST-0000")
    assert detect_memory_overlaps(eidos_dir, "", "", use_semantic=False) == {
        "duplicate_memories": [],
        "spec_overlaps": [],
    }
    assert search_memories(eidos_dir, "   ", use_semantic=False) == []
    assert list_memories(eidos_dir) == []

    # Add first active anchored memory
    res1 = add_memory(
        eidos_dir,
        title="Use real SQLite WAL tmp_path in storage tests",
        category="feedback",
        summary="Always use real temporary SQLite tmp_path DBs; mocked cursors hide FTS5 trigger bugs.",
        guidance="## Testing Rule\n- Pass tmp_path to ArchitecturalIndex\n```python\ndb = tmp_path / 'index.db'\n```",
        why="Mocked cursors previously masked an FTS5 delete-trigger bug.",
        entities=["CMP-CORE-DB"],
        paths=["src/core/db.py"],
        expires_at="2027-01-15",
        use_semantic=False,
        today=today,
    )
    mem1_id = res1["id"]
    mem1_path = Path(res1["path"])
    assert mem1_path.is_file()
    assert res1["verified_commit"] == head_sha
    assert res1["verified_at"] == today.isoformat()
    assert SpecValidator().validate_document(parse_spec_file(mem1_path)) == []

    # Verify duplicate BM25 detection when adding a closely paraphrased second memory
    res_dup = add_memory(
        eidos_dir,
        title="Avoid mocking SQLite cursors in storage tests",
        category="feedback",
        summary="Always use real temporary SQLite tmp_path DBs instead of mocking cursors so FTS5 triggers run.",
        entities=["CMP-CORE-DB"],
        paths=["src/core/db.py"],
        use_semantic=False,
        today=today,
    )
    assert any(d["id"] == mem1_id and d["method"] == "bm25" for d in res_dup["warnings"]["duplicate_memories"])

    # Verify BM25 spec overlap detection against POL-CORE-ZERO-AI / synthetic fixture invariant
    ov_spec = detect_memory_overlaps(
        eidos_dir,
        "Never call remote OpenAI Anthropic Gemini LLM APIs",
        "Deterministic offline CLI commands must never invoke remote OpenAI Anthropic or Gemini LLM APIs.",
        use_semantic=False,
    )
    inv_zero_ai = "INV-FIXTURE-ZERO-AI"  # eidos:fixture
    assert any(o["id"] in ("POL-CORE-ZERO-AI", inv_zero_ai) for o in ov_spec["spec_overlaps"])

    # Verify pre-computed semantic BLOB overlap path (both detect_memory_overlaps and audit_memories)
    idx = get_architectural_index(repo_root=repo, eidos_dir=eidos_dir)
    conn = idx._get_connection()
    unit_a = np.zeros(8, dtype=np.float32)
    unit_a[0] = 1.0
    unit_b = np.zeros(8, dtype=np.float32)
    unit_b[1] = 1.0
    blob_a = pack_embedding_f32(unit_a)
    blob_b = pack_embedding_f32(unit_b)
    for row in conn.execute("SELECT id, type FROM entities WHERE id IN (SELECT entity_id FROM specs_fts);").fetchall():
        blob = blob_a if row["id"] in (mem1_id, res_dup["id"], "POL-CORE-ZERO-AI") else blob_b
        conn.execute(
            "INSERT OR REPLACE INTO spec_embeddings (entity_id, content_sha256, model_id, embedding) VALUES (?, 'sha', 'mock', ?);",
            (row["id"], blob),
        )
    conn.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, '1');",
        (SEMANTIC_SYNC_COMPLETE_KEY,),
    )
    conn.commit()

    class _FakeEngine:
        def encode_texts(self, texts: list[str], *, is_query: bool = False) -> list[bytes]:
            return [blob_a for _ in texts]

    import eidos.core.index.semantic as sem_mod

    monkeypatch.setattr(sem_mod, "get_semantic_engine", lambda _eidos_dir: _FakeEngine())
    sem_ov = detect_memory_overlaps(eidos_dir, "Query", "Same vector", exclude_id=res_dup["id"], use_semantic=True)
    assert any(d["id"] == mem1_id and d["method"] == "semantic" for d in sem_ov["duplicate_memories"])
    assert any(o["id"] == "POL-CORE-ZERO-AI" and o["method"] == "semantic" for o in sem_ov["spec_overlaps"])

    sem_audit = audit_memories(eidos_dir, check_overlaps=True, use_semantic=True, today=today)
    assert any(p["method"] == "semantic" for p in sem_audit["duplicate_pairs"])
    assert any(o["spec_id"] == "POL-CORE-ZERO-AI" and o["method"] == "semantic" for o in sem_audit["spec_overlaps"])

    # Verify list_memories filters, search_memories, show_memory, and ambiguous prefix rejection
    assert len(list_memories(eidos_dir, category="feedback", status="active", entity="CMP-CORE-DB")) == 2
    assert list_memories(eidos_dir, category="reference") == []
    with pytest.raises(ValueError, match="Invalid category filter"):
        list_memories(eidos_dir, category="bad")
    with pytest.raises(ValueError, match="Invalid status filter"):
        list_memories(eidos_dir, status="bad")
    with pytest.raises(ValueError, match="Ambiguous"):
        find_memory_by_id_or_path(eidos_dir, "MEM-")

    hits = search_memories(eidos_dir, "SQLite FTS5 trigger", use_semantic=False, today=today)
    assert any(h["id"] == mem1_id and h["freshness"] == "fresh" for h in hits)

    detail = show_memory(eidos_dir, mem1_id, today=today)
    assert detail["id"] == mem1_id
    assert "Testing Rule" in detail["guidance"]
    assert "FTS5 delete-trigger" in detail["why"]
    assert detail["freshness"] == "fresh"

    # Reject non-memory spec file passed to show/verify/archive/promote
    dom_path = eidos_dir / "domains" / "core" / "domain.adoc"
    with pytest.raises(ValueError, match="not 'agent/memory'"):
        show_memory(eidos_dir, str(dom_path))

    # Verify re-stamping + clear_expires
    ver_res = verify_memory(eidos_dir, mem1_id, clear_expires=True, today=today + timedelta(days=2))
    assert ver_res["verified_at"] == (today + timedelta(days=2)).isoformat()
    assert ver_res["expires_at"] is None
    assert ver_res["freshness"] == "fresh"

    # Add stale (deleted path) + expired memories for audit coverage
    res_stale = add_memory(
        eidos_dir,
        title="Legacy helper cleanup",
        category="project",
        summary="Legacy cleanup note pointing at removed file.",
        paths=["src/core/removed.py"],
        use_semantic=False,
        today=today,
    )
    res_exp = add_memory(
        eidos_dir,
        title="Release freeze window",
        category="project",
        summary="Hold non-bugfix merges until release tag.",
        expires_at=(today - timedelta(days=1)).isoformat(),
        use_semantic=False,
        today=today,
    )

    # BM25 audit sees stale, expired, and duplicate pairs
    bm25_audit = audit_memories(eidos_dir, check_overlaps=True, use_semantic=False, today=today)
    assert bm25_audit["stale"] == 1
    assert bm25_audit["expired"] == 1
    assert bm25_audit["stale_memories"][0]["id"] == res_stale["id"]
    assert bm25_audit["expired_memories"][0]["id"] == res_exp["id"]
    assert len(bm25_audit["duplicate_pairs"]) >= 1

    # Archive duplicate memory with reason
    arch_res = archive_memory(eidos_dir, res_dup["id"], reason="Superseded by " + mem1_id)
    assert arch_res["status"] == "archived"
    assert "/memory/archive/" in f"/{arch_res['relative_path']}"
    assert Path(arch_res["path"]).is_file()
    assert not Path(res_dup["path"]).exists()
    assert "== Archive Note" in Path(arch_res["path"]).read_text(encoding="utf-8")

    # Promotion target resolution: standalone POL-*, child INV-*, embedded DEC-*, and negative checks
    assert resolve_promotion_target(eidos_dir, "POL-CORE-ZERO-AI", kind="policy")["kind"] == "product/policy"
    inv_db_wal = "INV-FIXTURE-DB-WAL"  # eidos:fixture
    assert resolve_promotion_target(eidos_dir, inv_db_wal, kind="invariant")["kind"] == "invariant"
    dec_res = resolve_promotion_target(eidos_dir, "DEC-CORE-SQLITE-REAL", kind="decision")
    assert dec_res["kind"] == "decision" and dec_res["owner_id"] == "CNT-CORE"

    with pytest.raises(ValueError, match="cannot be empty"):
        resolve_promotion_target(eidos_dir, "   ")
    with pytest.raises(ValueError, match="Invalid promotion kind"):
        resolve_promotion_target(eidos_dir, "DEC-CORE-SQLITE-REAL", kind="bogus")
    with pytest.raises(ValueError, match="expects a 'DEC-\\*'"):
        resolve_promotion_target(eidos_dir, "POL-CORE-ZERO-AI", kind="decision")
    with pytest.raises(ValueError, match="does not exist"):
        resolve_promotion_target(eidos_dir, "DEC-NONEXISTENT-999", kind="decision")

    # Promote with archive=True (moves to archive/) and archive=False (stays in memory/)
    prom_arch = promote_memory(eidos_dir, mem1_id, to_kind="decision", spec_id="DEC-CORE-SQLITE-REAL", archive=True)
    assert prom_arch["status"] == "promoted"
    assert prom_arch["promoted_to"] == "DEC-CORE-SQLITE-REAL"
    assert "/memory/archive/" in f"/{prom_arch['relative_path']}"
    assert SpecValidator().validate_document(parse_spec_file(Path(prom_arch["path"]))) == []

    prom_keep = promote_memory(
        eidos_dir,
        res_exp["id"],
        to_kind="policy",
        spec_id="POL-CORE-ZERO-AI",
        archive=False,
    )
    assert prom_keep["status"] == "promoted"
    assert "/memory/archive/" not in f"/{prom_keep['relative_path']}"

    # On-disk collision re-roll check (using existing memory dir)
    _, fn_unique = generate_memory_id("Use real SQLite WAL tmp_path in storage tests", eidos_dir)
    assert fn_unique != mem1_path.name


def test_memory_cli_subcommands_and_nonblocking_audit(tmp_path: Path) -> None:
    """Verify INV-MEMORY-CLI-AND-SKILL-PARITY: all 8 'eidos memory' CLI subcommands (Rich & --json), strictly non-blocking audit exit code 0, error handling, and root help/info parity."""
    _repo, eidos_dir, _head = _setup_memory_repo(tmp_path, name="cli_mem_repo")
    cli = CliRunner()

    # Root CLI mounts 'memory' under Knowledge Graph & AI Context and 'info' reports 17 schemas
    help_res = cli.invoke(app, ["--help"])
    assert help_res.exit_code == 0
    assert "memory" in help_res.stdout
    info_res = cli.invoke(app, ["info"])
    assert info_res.exit_code == 0
    assert "17 loaded" in info_res.stdout

    # Empty list & search
    assert cli.invoke(app, ["memory", "list", "--path", str(eidos_dir)]).exit_code == 0
    assert cli.invoke(app, ["memory", "search", "nothing", "--no-semantic", "--path", str(eidos_dir)]).exit_code == 0

    # 1. add (JSON + Rich + error on category user)
    bad_user = cli.invoke(
        app,
        [
            "memory",
            "add",
            "--title",
            "Personal tone",
            "--category",
            "user",
            "--summary",
            "Be brief",
            "--no-semantic",
            "--path",
            str(eidos_dir),
            "--json",
        ],
    )
    assert bad_user.exit_code == 1
    assert "error" in orjson.loads(bad_user.stdout)

    bad_user_rich = cli.invoke(
        app,
        [
            "memory",
            "add",
            "--title",
            "Personal tone",
            "--category",
            "user",
            "--summary",
            "Be brief",
            "--no-semantic",
            "--path",
            str(eidos_dir),
        ],
    )
    assert bad_user_rich.exit_code == 1
    assert "Error:" in bad_user_rich.stdout

    add_json = cli.invoke(
        app,
        [
            "memory",
            "add",
            "--title",
            "Use real SQLite WAL tmp_path in storage tests",
            "--category",
            "feedback",
            "--summary",
            "Always use real temporary SQLite tmp_path DBs; mocked cursors hide FTS5 trigger bugs.",
            "--guidance",
            "Pass tmp_path to ArchitecturalIndex.",
            "--why",
            "Mocked cursors hid an FTS5 bug.",
            "--entity",
            "CMP-CORE-DB",
            "--file",
            "src/core/db.py",
            "--expires",
            "2027-01-01",
            "--no-semantic",
            "--path",
            str(eidos_dir),
            "--json",
        ],
    )
    assert add_json.exit_code == 0, add_json.stdout
    m1 = orjson.loads(add_json.stdout)
    m1_id = m1["id"]

    # Second add in Rich mode triggers duplicate & spec overlap warnings cleanly (still exit 0)
    add_rich = cli.invoke(
        app,
        [
            "memory",
            "add",
            "--title",
            "Never call remote OpenAI Anthropic Gemini LLM APIs or mock SQLite tmp_path DBs",
            "--category",
            "project",
            "--summary",
            "Always use real temporary SQLite tmp_path DBs and never invoke remote OpenAI Anthropic or Gemini LLM APIs.",
            "--file",
            "src/core/deleted_target.py",
            "--expires",
            "2020-01-01",
            "--no-semantic",
            "--path",
            str(eidos_dir),
        ],
    )
    assert add_rich.exit_code == 0, add_rich.stdout
    assert "Saved team memory" in add_rich.stdout

    # 2. list (Rich + JSON + invalid filter exit 1)
    assert cli.invoke(app, ["memory", "list", "--path", str(eidos_dir)]).exit_code == 0
    lst_json = cli.invoke(app, ["memory", "list", "--path", str(eidos_dir), "--json"])
    assert lst_json.exit_code == 0
    assert orjson.loads(lst_json.stdout)["count"] == 2
    assert cli.invoke(app, ["memory", "list", "--category", "invalid", "--path", str(eidos_dir)]).exit_code == 1

    # 3. search (Rich + JSON)
    srch_rich = cli.invoke(app, ["memory", "search", "SQLite FTS5", "--no-semantic", "--path", str(eidos_dir)])
    assert srch_rich.exit_code == 0
    srch_json = cli.invoke(
        app, ["memory", "search", "SQLite FTS5", "--no-semantic", "--path", str(eidos_dir), "--json"]
    )
    assert srch_json.exit_code == 0
    assert orjson.loads(srch_json.stdout)["count"] >= 1

    # 4. show (Rich + JSON + missing ID exit 1)
    show_rich = cli.invoke(app, ["memory", "show", m1_id, "--path", str(eidos_dir)])
    assert show_rich.exit_code == 0
    assert "Guidance" in show_rich.stdout and "Why" in show_rich.stdout
    show_json = cli.invoke(app, ["memory", "show", m1_id, "--path", str(eidos_dir), "--json"])
    assert show_json.exit_code == 0
    assert orjson.loads(show_json.stdout)["id"] == m1_id
    assert cli.invoke(app, ["memory", "show", "MEM-MISSING", "--path", str(eidos_dir)]).exit_code == 1

    # 5. verify (Rich + JSON + invalid ID exit 1)
    ver_rich = cli.invoke(app, ["memory", "verify", m1_id, "--clear-expires", "--path", str(eidos_dir)])
    assert ver_rich.exit_code == 0
    ver_json = cli.invoke(app, ["memory", "verify", m1_id, "--path", str(eidos_dir), "--json"])
    assert ver_json.exit_code == 0
    assert orjson.loads(ver_json.stdout)["freshness"] == "fresh"
    assert cli.invoke(app, ["memory", "verify", "MEM-MISSING", "--path", str(eidos_dir)]).exit_code == 1

    # 6. audit (Rich + JSON: ALWAYS exit code 0 even with expired/duplicate/overlap findings!)
    aud_rich = cli.invoke(app, ["memory", "audit", "--no-semantic", "--path", str(eidos_dir)])
    assert aud_rich.exit_code == 0
    assert "Team Memory Audit" in aud_rich.stdout
    aud_json = cli.invoke(app, ["memory", "audit", "--no-semantic", "--path", str(eidos_dir), "--json"])
    assert aud_json.exit_code == 0
    aud_data = orjson.loads(aud_json.stdout)
    assert aud_data["total"] == 2
    assert aud_data["expired"] >= 1

    # 7. promote (Rich + JSON + error on nonexistent spec-id)
    bad_prom = cli.invoke(
        app,
        [
            "memory",
            "promote",
            m1_id,
            "--to",
            "decision",
            "--spec-id",
            "DEC-MISSING-999",
            "--path",
            str(eidos_dir),
            "--json",
        ],
    )
    assert bad_prom.exit_code == 1

    prom_rich = cli.invoke(
        app,
        [
            "memory",
            "promote",
            m1_id,
            "--to",
            "decision",
            "--spec-id",
            "DEC-CORE-SQLITE-REAL",
            "--no-archive",
            "--path",
            str(eidos_dir),
        ],
    )
    assert prom_rich.exit_code == 0, prom_rich.stdout
    assert "Promoted memory" in prom_rich.stdout

    # Show promoted memory in Rich mode covers Promoted To line
    show_prom = cli.invoke(app, ["memory", "show", m1_id, "--path", str(eidos_dir)])
    assert show_prom.exit_code == 0 and "DEC-CORE-SQLITE-REAL" in show_prom.stdout

    prom_json = cli.invoke(
        app,
        [
            "memory",
            "promote",
            m1_id,
            "--to",
            "decision",
            "--spec-id",
            "DEC-CORE-SQLITE-REAL",
            "--path",
            str(eidos_dir),
            "--json",
        ],
    )
    assert prom_json.exit_code == 0
    assert orjson.loads(prom_json.stdout)["promoted_to"] == "DEC-CORE-SQLITE-REAL"

    # 8. archive (Rich + JSON + missing ID exit 1)
    other_id = next(m["id"] for m in orjson.loads(lst_json.stdout)["memories"] if m["id"] != m1_id)
    arch_rich = cli.invoke(
        app,
        ["memory", "archive", other_id, "--reason", "Expired window", "--path", str(eidos_dir)],
    )
    assert arch_rich.exit_code == 0
    arch_json = cli.invoke(app, ["memory", "archive", other_id, "--path", str(eidos_dir), "--json"])
    assert arch_json.exit_code == 0
    assert orjson.loads(arch_json.stdout)["status"] == "archived"
    assert cli.invoke(app, ["memory", "archive", "MEM-MISSING", "--path", str(eidos_dir)]).exit_code == 1


def test_memory_deep_review_hardening_regressions(tmp_path: Path) -> None:
    """Comprehensive regression suite covering deep-review security, graph, freshness, sanitizer, CLI, and portal fixes."""
    import os
    import subprocess
    from datetime import date

    import pytest
    from typer.testing import CliRunner

    from eidos.cli.main import app
    from eidos.core.index.context_memories import (
        _check_target_paths,
        attach_context_memories,
        resolve_memory_freshness_batch,
    )
    from eidos.core.index.graph_index import get_architectural_index
    from eidos.core.index.storage import load_memory_freshness_cache, save_memory_freshness_cache
    from eidos.core.lifecycle.audit import AuditEngine
    from eidos.core.lifecycle.delta_verifier import detect_delta_conflicts
    from eidos.core.lifecycle.initializer import init_repository
    from eidos.core.lifecycle.memory_engine import (
        add_memory,
        archive_memory,
        audit_memories,
        find_memory_by_id_or_path,
        list_memories,
        promote_memory,
        sanitize_memory_asciidoc_body,
        search_memories,
        show_memory,
        verify_memory,
    )
    from eidos.core.lifecycle.scaffolder import create_spec, delete_spec, list_specs
    from eidos.core.spec.models import infer_entity_type_from_path
    from eidos.core.spec.parser import parse_spec_file
    from eidos.core.spec.validator import SpecValidator
    from eidos.plugin.hooks import handle_stop_hook
    from eidos.portal.generator import get_portal_data
    from eidos.vcs.diff import classify_changed_files

    repo = tmp_path / "hardening-repo"
    init_repository(repo, title="Hardening Repo")
    eidos_dir = repo / "eidos"

    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "core.py").write_text("def run() -> int:\n    return 1\n", encoding="utf-8")
    (repo / "scripts").mkdir(parents=True, exist_ok=True)
    (repo / "scripts" / "deploy_a.sh").write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    (repo / "tests").mkdir(parents=True, exist_ok=True)
    (repo / "tests" / "test_core.py").write_text("def test_run() -> None:\n    assert True\n", encoding="utf-8")

    (eidos_dir / "architecture" / "containers" / "cnt-core.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-CORE\n"
        "title: Core Service\n"
        "status: active\n"
        "container_type: service\n"
        "runtime: Python 3.12\n"
        "source_path: src\n"
        "---\n"
        "= Core Service\n",
        encoding="utf-8",
    )
    (eidos_dir / "architecture" / "components" / "cmp-core.adoc").write_text(
        "---\n"
        "type: tech/component\n"
        "id: CMP-CORE\n"
        "title: Core Runner\n"
        "container: CNT-CORE\n"
        "status: active\n"
        "source_path: src/core.py\n"
        "---\n"
        "= Core Runner\n\n== Public Surface\n* `run()`\n",
        encoding="utf-8",
    )
    inv_pfx = "INV"
    (eidos_dir / "policies" / "pol-base.adoc").write_text(
        "---\n"
        "type: product/policy\n"
        "id: POL-BASE\n"
        "title: Base Policy\n"
        "category: governance\n"
        "severity: blocking\n"
        "status: active\n"
        "invariants:\n"
        f"  - id: {inv_pfx}-BASE-RULE\n"
        "    rule: Keep runtime deterministic.\n"
        "    test_binding: tests/test_core.py::test_run\n"
        "---\n"
        "= Base Policy\n",
        encoding="utf-8",
    )

    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Dev"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    # 1. Path traversal & symlink escape blocked BEFORE reading/mutating external files
    external_mem = tmp_path / "external-secret-mem.adoc"
    external_mem.write_text(
        "---\ntype: agent/memory\nid: MEM-EXT-1111\ntitle: External\ncategory: feedback\n"
        "status: active\nsummary: External file.\nverified_at: '2026-11-24'\n---\n= External\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not 'agent/memory'"):
        find_memory_by_id_or_path(eidos_dir, str(external_mem))
    (eidos_dir / "memory").mkdir(parents=True, exist_ok=True)
    sym_mem = eidos_dir / "memory" / "mem-sym-1111.adoc"
    os.symlink(external_mem, sym_mem)
    with pytest.raises(ValueError, match="symlink"):
        find_memory_by_id_or_path(eidos_dir, str(sym_mem))
    assert list_memories(eidos_dir) == []
    sym_mem.unlink()

    # 2. Phase 1 fixes: Windows backslash diff, eidos/memory/deltas/ isolation, infer_entity_type_from_path,
    #    calendar date validation, placement validation, list_specs/delete_spec guards
    win_cls = classify_changed_files(
        [r"eidos\memory\mem-win.adoc", "eidos/memory/deltas/mem-nested.adoc"],
        repo_root=repo,
        eidos_dir=eidos_dir,
        package_paths=[],
    )
    assert len(win_cls.memory_files) == 2
    assert win_cls.delta_files == []
    assert win_cls.other_files == []

    assert infer_entity_type_from_path("eidos/memory/deltas/mem-x.adoc")[0] == "MEM-"  # type: ignore[index]
    assert infer_entity_type_from_path("eidos/memory") is None
    assert infer_entity_type_from_path("eidos/domains/eidos/memory/domain.adoc")[0] == "DOM-"  # type: ignore[index]

    bad_date_file = eidos_dir / "memory" / "mem-baddate.adoc"
    bad_date_file.write_text(
        "---\ntype: agent/memory\nid: MEM-BADDATE-1111\ntitle: Bad date\ncategory: project\n"
        "status: active\nsummary: Invalid date.\nverified_at: '2026-02-31'\npromoted_to: DEC-FOO\n---\n= Bad\n",
        encoding="utf-8",
    )
    bad_errs = SpecValidator().validate_document(parse_spec_file(bad_date_file), root_dir=eidos_dir)
    assert any("2026-02-31" in e or "date" in e for e in bad_errs)
    assert any("promoted_to" in e for e in bad_errs)
    bad_date_file.unlink()

    stray_mem = eidos_dir / "architecture" / "mem-stray.adoc"
    stray_mem.write_text(
        "---\ntype: agent/memory\nid: MEM-STRAY-1111\ntitle: Stray\ncategory: feedback\n"
        "status: active\nsummary: Misplaced.\nverified_at: '2026-11-24'\n---\n= Stray\n",
        encoding="utf-8",
    )
    stray_errs = SpecValidator().validate_document(parse_spec_file(stray_mem), root_dir=eidos_dir)
    assert any("eidos/memory/" in e for e in stray_errs)
    stray_mem.unlink()

    with pytest.raises(ValueError, match="eidos memory add"):
        create_spec(eidos_dir, "agent-memory", "MEM-NO", "No")
    with pytest.raises(ValueError, match="eidos memory list"):
        list_specs(eidos_dir, type_filter="memory")

    # 3. Sanitizer hardening (multiline title, preprocessor directives in fences, ---- inside fences, ordered lists,
    #    native AsciiDoc blocks, standalone dividers, callouts, c#/f# langs)
    sanitized = sanitize_memory_asciidoc_body(
        '```python title="demo.py"\ninclude::/etc/passwd[]\nifdef::env[]\n----\nprint(1)\n```\n\n'
        "```c#\nvar x = 1;\n```\n\n```f#\nlet y = 2\n```\n\n"
        "----\ninclude::/etc/hosts[]\necho hi\n----\n\n"
        "-----\n\n"
        "====\ninclude::/etc/shadow[]\ninside example\n====\n\n"
        "|===\n\n"
        "1. first\n3. third\n\n<1> callout item\n\nHeading\n=======\n"
    )
    assert "\\include::/etc/passwd[]" in sanitized
    assert "\\ifdef::env[]" in sanitized
    assert "[source,csharp]" in sanitized and "[source,fsharp]" in sanitized
    assert "-----" in sanitized
    assert "`include::/etc/shadow[]`" in sanitized
    assert "====\n`include::/etc/shadow[]`\ninside example\n====" in sanitized
    assert ". first" in sanitized and ". third" in sanitized
    assert "\\<1> callout item" in sanitized
    assert "`|===`" in sanitized
    assert "=======" not in sanitized

    # 4. Pure raw-BM25 overlap (no -1000/-50 false positives when referencing same entity ID)
    m1 = add_memory(
        eidos_dir,
        title="Always run pnpm build before tagging",
        category="feedback",
        summary="Build frontend assets via pnpm before release tagging.",
        guidance="Use [source,bash] and never touch [/tmp] directly.",
        entities=["CMP-CORE"],
        paths=["scripts/deploy_*.sh"],
    )
    m2 = add_memory(
        eidos_dir,
        title="Configure debug logging for CMP-CORE\nwith newline",
        category="feedback",
        summary="Enable structured JSON logging on staging.",
        entities=["CMP-CORE"],
    )
    assert "\n" not in m2["title"]
    assert m2["warnings"]["duplicate_memories"] == []
    assert m2["warnings"]["spec_overlaps"] == []

    # 5. CLI Rich MarkupError safety on [/tmp] and [source,bash] + non-existent search path
    cli = CliRunner()
    res_show = cli.invoke(app, ["memory", "show", m1["id"], "--path", str(eidos_dir)])
    assert res_show.exit_code == 0
    assert "[/tmp]" in res_show.stdout
    nonexist = tmp_path / "does-not-exist-eidos"
    assert search_memories(nonexist, "anything") == []
    assert not nonexist.exists()

    # 6. Lifecycle edge cases: draft freshness, list --status archived/promoted, archive collision, verify archived blocked
    m_draft = add_memory(
        eidos_dir,
        title="Draft note",
        category="project",
        summary="Still validating this rollout window.",
        status="draft",
    )
    assert show_memory(eidos_dir, m_draft["id"])["freshness"] == "draft"
    assert not any(sp["id"] == m_draft["id"] for sp in list_specs(eidos_dir))
    ok_del, msg_del, _ = delete_spec(eidos_dir, m_draft["id"])
    assert ok_del is False and "eidos memory archive" in msg_del

    m_prom = add_memory(
        eidos_dir,
        title="Deterministic policy reminder",
        category="feedback",
        summary="Matches base policy.",
    )
    prom_res = promote_memory(eidos_dir, m_prom["id"], to_kind="policy", spec_id="POL-BASE", archive=True)
    assert len(list_memories(eidos_dir, status="promoted")) == 1
    with pytest.raises(ValueError, match="already promoted"):
        archive_memory(eidos_dir, m_prom["id"])
    with pytest.raises(ValueError, match="archived or promoted"):
        verify_memory(eidos_dir, m_prom["id"])
    with pytest.raises(ValueError, match="Cannot combine"):
        verify_memory(eidos_dir, m1["id"], expires_at="2027-01-01", clear_expires=True)
    with pytest.raises(ValueError, match="Invalid --expires"):
        verify_memory(eidos_dir, m1["id"], expires_at="   ")
    assert verify_memory(eidos_dir, m_draft["id"])["freshness"] == "draft"
    with pytest.raises(ValueError, match="archived"):
        find_memory_by_id_or_path(eidos_dir, prom_res["path"], include_archived=False)

    # Archive collision creates unique filename instead of overwriting existing archive file
    arch_dir = eidos_dir / "memory" / "archive"
    arch_dir.mkdir(parents=True, exist_ok=True)
    m2_path = Path(m2["path"])
    collision_file = arch_dir / m2_path.name
    collision_content = (
        "---\ntype: agent/memory\nid: MEM-OLD-ARCHIVED-0001\ntitle: Old Archived\ncategory: feedback\n"
        "status: archived\nsummary: Existing archived file.\nverified_at: '2026-01-01'\n---\n= Old\n"
    )
    collision_file.write_text(collision_content, encoding="utf-8")
    arch_res = archive_memory(eidos_dir, m2["id"], reason="Retired note")
    assert Path(arch_res["path"]) != collision_file
    assert collision_file.read_text(encoding="utf-8") == collision_content
    collision_file.unlink()

    # 7. Graph isolation: DeletionAuditor, blast-radius Hop-2/archived, delta_verifier, index health memory_audit
    idx = get_architectural_index(repo_root=repo, eidos_dir=eidos_dir)
    idx.sync(force=True, auto_semantic=False)
    conn = idx._get_connection()
    incoming = idx.get_incoming_references("CMP-CORE")
    assert not any(str(r.get("source_id", "")).startswith("MEM-") for r in incoming)
    auditor = AuditEngine(repo_root=repo, eidos_dir=eidos_dir)
    assert auditor._check_graph_integrity({"CNT-CORE", "CMP-CORE"}, "DLT-DEL-TEST", index=idx) == []

    walk = {n["id"]: n for n in idx.get_k_hop_neighborhood("CNT-CORE", max_depth=2)}
    assert m1["id"] not in walk  # m1 anchored to CMP-CORE (Hop 2 from CNT-CORE) must not leak
    assert m2["id"] not in walk  # archived m2 must not leak

    # Two deltas touching CMP-CORE & CNT-CORE don't false-conflict on shared memory anchor
    (eidos_dir / "deltas" / "dlt-a.adoc").write_text(
        "---\ntype: delta/mutation\nid: DLT-2026-991\ntitle: A\nstatus: proposed\n"
        "intent:\n  kind: feature\n  motivation: A\nmutations:\n  - operation: modify\n    target: CMP-CORE\n---\n= A\n",
        encoding="utf-8",
    )
    (eidos_dir / "deltas" / "dlt-b.adoc").write_text(
        "---\ntype: delta/mutation\nid: DLT-2026-992\ntitle: B\nstatus: proposed\n"
        "intent:\n  kind: feature\n  motivation: B\nmutations:\n  - operation: modify\n    target: POL-BASE\n---\n= B\n",
        encoding="utf-8",
    )
    conflicts = detect_delta_conflicts(eidos_dir)
    assert not any(
        any(str(eid).startswith("MEM-") for eid in ov.get("shared_neighbors", []))
        for ov in conflicts.get("blast_radius_overlaps", [])
    )

    health = idx.get_governance_health()
    assert "memory_audit" in health
    assert health["memory_audit"]["total"] >= 4
    assert health["memory_audit"]["promoted"] == 1
    assert health["memory_audit"]["archived"] == 1

    # 8. Freshness cache: glob deletion invalidation, commit_drift_threshold digest, '**' pathspec, :memory: no-file
    m1_fm = parse_spec_file(Path(m1["path"])).frontmatter
    conn = idx._get_connection()
    cat_ids = {str(r[0]) for r in conn.execute("SELECT id FROM entities;").fetchall()}
    r_before = resolve_memory_freshness_batch(
        repo, [(m1["id"], m1_fm)], cat_ids, conn=conn, db_path=idx.db_path, today=date(2026, 11, 25)
    )
    assert r_before[m1["id"]][0] == "fresh"
    (repo / "scripts" / "deploy_a.sh").unlink()
    r_after = resolve_memory_freshness_batch(
        repo, [(m1["id"], m1_fm)], cat_ids, conn=conn, db_path=idx.db_path, today=date(2026, 11, 25)
    )
    assert r_after[m1["id"]][0] == "stale"

    missing_star, specs_star = _check_target_paths(repo, ["**"])
    assert missing_star == [] and "" not in specs_star

    orig_cwd = Path.cwd()
    os.chdir(tmp_path)
    try:
        save_memory_freshness_cache(Path(":memory:"), [("MEM-X", "abc", "def", "fresh", "ok", "2026-11-25")])
        assert load_memory_freshness_cache(Path(":memory:")) == {}
        assert not (tmp_path / ":memory:").exists()
    finally:
        os.chdir(orig_cwd)

    # Honest empty slices: unmatched glob requested_files does not trigger wildcard memory
    assert (
        attach_context_memories(
            conn,
            repo,
            clean_files=[],
            requested_files=["nonexistent/*.py", "/tmp/outside.py"],
            has_explicit_query=True,
            has_architectural_matches=False,
        )
        == []
    )

    # 9. Malformed targets frontmatter never crashes list_memories, audit_memories, or get_portal_data
    malformed = eidos_dir / "memory" / "mem-malformed-targets.adoc"
    malformed.write_text(
        "---\ntype: agent/memory\nid: MEM-MALFORMED-9999\ntitle: Malformed targets\ncategory: feedback\n"
        "status: active\nsummary: Non-list targets.\ntargets:\n  entities: 123\n  paths: true\n"
        "verified_at: '2026-11-24'\n---\n= Malformed\n",
        encoding="utf-8",
    )
    audit_res = audit_memories(eidos_dir, check_overlaps=True, use_semantic=False)
    assert audit_res["total"] >= 5
    pdata = get_portal_data(eidos_dir)
    assert any(m["id"] == "MEM-MALFORMED-9999" for m in pdata["memories"])
    assert pdata["validation"]["is_clean"] is False  # schema error reported cleanly without HTTP 500

    # Stop hook does not short-circuit on schema-invalid memory edit
    stop_res = handle_stop_hook({"modified_files": [str(malformed.relative_to(repo))]}, repo)
    assert stop_res != {"decision": "allow"}
    malformed.unlink()
