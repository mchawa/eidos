"""Regression tests for SQLite sync lock serialization, .NET/C# indexing, Public Surface scoping/LRU cache, and CLI sync controls."""

from __future__ import annotations

import contextlib
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import orjson
from typer.testing import CliRunner

from eidos.cli.main import app
from eidos.core.index.ast_indexer import (
    _is_component_surface_symbol,
    _is_public_symbol,
    _strip_commented_out_code_blocks,
    get_ast_indexer,
)
from eidos.core.index.graph_index import ArchitecturalIndex
from eidos.core.index.semantic import _is_semantic_sync_locked_by_other, _semantic_sync_lock, sync_spec_embeddings
from eidos.core.index.storage import (
    _advisory_db_lock,
    load_test_binding_cache,
    open_cache_connection,
    save_test_binding_cache,
)
from eidos.core.index.sync import check_index_dirty
from eidos.core.spec.validator import SpecValidator
from eidos.core.workspace.project import is_dotnet_bin_dir, is_polyglot_test_file
from eidos.vcs.diff import classify_changed_files, collect_polyglot_test_files
from eidos.vcs.invariants import _verify_single_invariant, detect_orphan_invariants, extract_invariants_and_verify

runner = CliRunner()


def test_parallel_sync_lock_and_cli_no_semantic_json(tmp_path: Path, monkeypatch) -> None:
    """Verify INV-INDEX-SYNC-CLI-CONTROLS and INV-INDEX-CONCURRENT-WAL-SAFETY:
    concurrent CLI sync/context calls serialize without IntegrityError, check_index_dirty handles
    mtime refreshes safely, and 'eidos index sync --no-semantic --json' avoids duplicate ONNX runs."""
    repo = tmp_path / "parallel_repo"
    eidos_dir = repo / "eidos"
    cnt_dir = eidos_dir / "architecture" / "containers"
    cmp_dir = eidos_dir / "architecture" / "components"
    dom_dir = eidos_dir / "domains" / "core" / "capabilities"
    src_dir = repo / "src" / "app"
    tests_dir = repo / "tests"
    for d in (cnt_dir, cmp_dir, dom_dir, src_dir, tests_dir):
        d.mkdir(parents=True, exist_ok=True)

    inv_pfx = "INV"
    (src_dir / "service.py").write_text("def process_item(x: int) -> int:\n    return x + 1\n", encoding="utf-8")
    (tests_dir / "test_service.py").write_text(
        f"def test_process_item():\n    # {inv_pfx}-CORE-001\n    assert True\n", encoding="utf-8"
    )
    (eidos_dir / "system.adoc").write_text(
        "---\ntype: product/system\nid: SYS-PAR\ntitle: Parallel System\nstatus: active\narchetypes: [service]\n---\n= Sys\n",
        encoding="utf-8",
    )
    (eidos_dir / "domains" / "core" / "domain.adoc").write_text(
        "---\ntype: product/domain\nid: DOM-CORE\ntitle: Core Domain\nstatus: active\nclassification: core\nowners: [Team]\n---\n= Dom\n",
        encoding="utf-8",
    )
    (cnt_dir / "app.adoc").write_text(
        "---\ntype: tech/container\nid: CNT-APP\ntitle: App Container\nstatus: active\ncontainer_type: service\nruntime: Python\nsource_path: src/app\ndomain: DOM-CORE\n---\n= App\n",
        encoding="utf-8",
    )
    (cmp_dir / "svc.adoc").write_text(
        "---\ntype: tech/component\nid: CMP-SVC\ntitle: Service Component\ncontainer: CNT-APP\nstatus: active\nsource_path: src/app/service.py\n---\n= Svc\n",
        encoding="utf-8",
    )
    (dom_dir / "cap-core.adoc").write_text(
        f"---\ntype: product/capability\nid: CAP-CORE-001\ntitle: Core Cap\ndomain: DOM-CORE\nstatus: active\ntechnical:\n  containers: [CNT-APP]\n  components: [CMP-SVC]\n  invariants:\n    - id: {inv_pfx}-CORE-001\n      rule: Must increment.\n      test_binding: tests/test_service.py::test_process_item\n---\n= Cap\n",
        encoding="utf-8",
    )

    # 1. Re-entrant same-thread advisory lock + :memory:/None fast path
    db_file = eidos_dir / ".cache" / "index.db"
    with _advisory_db_lock(None) as ok_none, _advisory_db_lock(Path(":memory:")) as ok_mem:
        assert ok_none is True and ok_mem is True
    with _advisory_db_lock(db_file) as ok1:
        assert ok1 is True
        with _advisory_db_lock(db_file) as ok2:
            assert ok2 is True

    # 2. Parallel multi-process CLI calls racing on a dirty / forced index
    procs: list[subprocess.Popen[str]] = []
    cmds = [
        [sys.executable, "-m", "eidos.cli.main", "index", "sync", "--force", "--no-semantic", "--path", str(eidos_dir)],
        [sys.executable, "-m", "eidos.cli.main", "index", "sync", "--force", "--no-semantic", "--path", str(eidos_dir)],
        [sys.executable, "-m", "eidos.cli.main", "context", "-c", "CNT-APP", "--path", str(eidos_dir), "--json"],
        [sys.executable, "-m", "eidos.cli.main", "index", "search", "increment", "--no-semantic", "--path", str(eidos_dir), "--json"],
        [sys.executable, "-m", "eidos.cli.main", "index", "sync", "--no-semantic", "--path", str(eidos_dir), "--json"],
        [sys.executable, "-m", "eidos.cli.main", "context", "-f", "src/app/service.py", "--path", str(eidos_dir), "--json"],
    ]
    for cmd in cmds:
        procs.append(
            subprocess.Popen(
                cmd,
                cwd=repo,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        )
    for proc in procs:
        out, err = proc.communicate(timeout=30)
        assert proc.returncode == 0, f"Parallel CLI call failed:\nSTDOUT:\n{out}\nSTDERR:\n{err}"
        assert "IntegrityError" not in err and "UNIQUE constraint failed" not in err

    idx = ArchitecturalIndex(db_path=db_file, repo_root=repo, eidos_dir=eidos_dir)
    try:
        # 3. Verify mtime-only bump refreshes in check_index_dirty without full reindex
        svc_file = src_dir / "service.py"
        old_st = svc_file.stat()
        os.utime(svc_file, (old_st.st_atime, old_st.st_mtime + 30.0))
        dirty, to_up, to_del = check_index_dirty(idx)
        assert dirty is False and to_up == [] and to_del == []

        # 4. Verify CLI 'eidos index sync --no-semantic --json' and default semantic '--json'
        res_clean = runner.invoke(app, ["index", "sync", "--no-semantic", "--json", "--path", str(eidos_dir)])
        assert res_clean.exit_code == 0, res_clean.output
        clean_data = orjson.loads(res_clean.stdout)
        assert clean_data["status"] == "clean"
        assert clean_data["semantic_enabled"] is False
        assert clean_data["semantic"] == {"status": "skipped", "reason": "disabled_by_flag"}

        # Force rebuild with default semantic + --json triggers foreground semantic sync ONCE without auto background spawn
        spawn_calls: list[int] = []
        sync_sem_calls: list[bool] = []
        monkeypatch.setattr(
            "eidos.core.index.semantic._trigger_auto_semantic_refresh",
            lambda *_a, **_kw: spawn_calls.append(1),
        )
        monkeypatch.setattr(
            ArchitecturalIndex,
            "get_semantic_status",
            lambda _self: {"runtime_available": True, "model_ready": True, "stale_or_missing_embeddings": 3},
        )
        monkeypatch.setattr(
            ArchitecturalIndex,
            "sync_semantic",
            lambda _self, force=False, **_kw: (
                sync_sem_calls.append(force),
                {"encoded_entities": 3, "reused_entities": 0, "pruned_orphans": 0, "duration_ms": 1.25},
            )[1],
        )

        res_sem = runner.invoke(app, ["index", "sync", "--force", "--json", "--path", str(eidos_dir)])
        assert res_sem.exit_code == 0, res_sem.output
        sem_data = orjson.loads(res_sem.stdout)
        assert sem_data["status"] == "rebuilt"
        assert sem_data["forced"] is True
        assert sem_data["semantic_enabled"] is True
        assert sem_data["semantic"]["status"] == "synchronized"
        assert sem_data["semantic"]["encoded_entities"] == 3
        assert spawn_calls == []
        assert len(sync_sem_calls) == 1

        # Foreground semantic error is surfaced cleanly in --json without failing sync exit code
        def _raise_sem(*_a, **_kw):
            raise RuntimeError("onnx boom")

        monkeypatch.setattr(ArchitecturalIndex, "sync_semantic", _raise_sem)
        res_sem_err = runner.invoke(app, ["index", "sync", "--force", "--json", "--path", str(eidos_dir)])
        assert res_sem_err.exit_code == 0, res_sem_err.output
        err_data = orjson.loads(res_sem_err.stdout)
        assert err_data["semantic"]["status"] == "error"
        assert "onnx boom" in str(err_data["semantic"]["error"])

        # 5. _semantic_sync_lock truncates lockfile cleanly so _is_semantic_sync_locked_by_other does not stick
        with _semantic_sync_lock(eidos_dir):
            pass
        assert _is_semantic_sync_locked_by_other(eidos_dir) is False

        # Foreground sync_spec_embeddings with custom encoder skips redundant encoding when fresh
        conn = idx._get_connection()
        enc_calls: list[int] = []

        def _fake_encode(texts: list[str]) -> list[bytes]:
            enc_calls.append(len(texts))
            return [b"\x00" * (1024 * 4) for _ in texts]

        r1 = sync_spec_embeddings(conn, eidos_dir, force=False, encode_fn=_fake_encode)
        calls_after_r1 = len(enc_calls)
        r2 = sync_spec_embeddings(conn, eidos_dir, force=False, encode_fn=_fake_encode)
        assert r1["encoded_entities"] >= 1
        assert sum(enc_calls) == r1["encoded_entities"]
        assert r2["encoded_entities"] == 0 and r2["reused_entities"] == r1["total_entities"]
        assert len(enc_calls) == calls_after_r1
    finally:
        idx.close()


def test_dotnet_test_detection_namespace_edges_and_public_surface(tmp_path: Path) -> None:
    """Verify INV-DOTNET-POLYGLOT-PARITY and INV-SPEC-GRAPH-INTEGRITY:
    .NET test classification & AST bindings, C# namespace + .csproj dependency derivation,
    C# property/record/event/attribute extraction, ':' inheritance, scoped Public Surface visibility,
    prose/extension filtering, OpenAPI boundary scoping, and 8,192-entry mtime_ns LRU caching."""
    repo = tmp_path / "dotnet_solution"
    eidos_dir = repo / "eidos"
    cnt_dir = eidos_dir / "architecture" / "containers"
    cmp_dir = eidos_dir / "architecture" / "components"
    ctr_dir = eidos_dir / "architecture" / "contracts"
    dom_dir = eidos_dir / "domains" / "billing" / "capabilities"
    gbf_model = repo / "GBFSolution" / "GBF.Model"
    gbf_svc = repo / "GBFSolution" / "GBF.Service"
    gbf_tests = repo / "GBFSolution" / "GBF.Tests"
    gw_tests = repo / "GBFSolution" / "MTSSupplyChainGateway.UnitTests"
    for d in (
        cnt_dir,
        cmp_dir,
        ctr_dir,
        dom_dir,
        gbf_model / "bin" / "Debug" / "net8.0",
        gbf_model / "obj" / "Debug" / "net8.0",
        gbf_svc,
        gbf_tests,
        gw_tests,
        repo / "src" / "bin",
    ):
        d.mkdir(parents=True, exist_ok=True)

    # Rust src/bin should NOT be treated as a .NET bin directory
    (repo / "src" / "bin" / "tool.rs").write_text("fn main() {}\n", encoding="utf-8")
    assert is_dotnet_bin_dir(repo / "src" / "bin") is False
    assert is_dotnet_bin_dir(gbf_model / "bin") is True

    # Build output files inside bin/ and obj/ must be ignored everywhere
    (gbf_model / "bin" / "Debug" / "net8.0" / "GeneratedOutput.cs").write_text(
        "public class BinArtifactOnly {}\n", encoding="utf-8"
    )
    (gbf_model / "obj" / "Debug" / "net8.0" / "GlobalUsings.g.cs").write_text(
        "public class ObjArtifactOnly {}\n", encoding="utf-8"
    )

    (gbf_model / "GBF.Model.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>\n',
        encoding="utf-8",
    )
    (gbf_model / "BaseEntity.cs").write_text(
        "namespace GBF.Model.Library;\n\n"
        "public abstract class BaseEntity {\n"
        "    public int Id { get; set; }\n"
        "    public string Describe() => Id.ToString();\n"
        "    protected void HiddenBase() {}\n"
        "}\n",
        encoding="utf-8",
    )
    (gbf_model / "Party.cs").write_text(
        "using System;\n"
        "using System.Text.Json.Serialization;\n\n"
        "namespace GBF.Model.Library {\n"
        "    public record PartyRecord(string PartyId, string Code);\n\n"
        "    public class Party : BaseEntity, IDisposable {\n"
        '        public const string DefaultCurrency = "USD";\n'
        '        [JsonPropertyName("name")]\n'
        '        public string Name { get; set; } = "";\n'
        "        public string DisplayName => Name;\n"
        "        public string this[int index] => Name;\n"
        "        internal int InternalRevision { get; set; }\n"
        '        private string SecretToken { get; set; } = "";\n'
        "        void UnannotatedPrivate() {}\n"
        "        public event EventHandler? Updated;\n"
        "        public void Dispose() {}\n"
        "        private class PrivateNested {\n"
        "            public void SecretNested() {}\n"
        "        }\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    (gbf_svc / "GBF.Service.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk">\n'
        "  <ItemGroup>\n"
        '    <ProjectReference Include="..\\GBF.Model\\GBF.Model.csproj" />\n'
        "  </ItemGroup>\n"
        "</Project>\n",
        encoding="utf-8",
    )
    (gbf_svc / "PartyService.cs").write_text(
        "using GBF.Model.Library;\n\n"
        "namespace GBF.Service;\n\n"
        '[Route("api/[controller]")]\n'
        "public class PartyService {\n"
        '    [HttpPost("create")]\n'
        "    public void CreateParty() {}\n"
        "    internal void InternalSync() {}\n"
        "    private void SecretAudit() {}\n"
        "}\n",
        encoding="utf-8",
    )
    (gbf_svc / "PartyServiceSharedNs.cs").write_text(
        "namespace GBF.Model.Library {\n"
        "    internal static class PartyExt {}\n"
        "}\n",
        encoding="utf-8",
    )
    rust_dir = repo / "src" / "rust_store"
    rust_dir.mkdir(parents=True, exist_ok=True)
    (rust_dir / "lib.rs").write_text(
        "pub trait SuperStore {\n"
        "    fn super_op(&self) -> usize;\n"
        "}\n"
        "pub trait SubStore: SuperStore + Send {\n"
        "    fn sub_op(&self);\n"
        "}\n",
        encoding="utf-8",
    )

    inv_pfx = "INV"
    (gbf_tests / "PartyTests.cs").write_text(
        "using Xunit;\n"
        "namespace GBF.Tests;\n"
        "public class PartyTests {\n"
        f"    // {inv_pfx}-DOTNET-001\n"
        '    [Fact(DisplayName = "Validates party")]\n'
        "    public void ValidatesParty() {}\n\n"
        f"    // {inv_pfx}-DOTNET-002\n"
        "    [Theory]\n"
        "    public void ValidatesPartyCode(string code) {}\n\n"
        f"    // {inv_pfx}-DOTNET-SKIP\n"
        '    [Fact(Skip = "Disabled temporarily")]\n'
        "    public void SkippedFactTest() {}\n\n"
        f"    // {inv_pfx}-DOTNET-IGNORE\n"
        '    [Ignore("Disabled NUnit")]\n'
        "    public void IgnoredNUnitTest() {}\n"
        "}\n",
        encoding="utf-8",
    )
    (gbf_tests / "PartyHelper.cs").write_text("namespace GBF.Tests;\npublic static class PartyHelper {}\n", encoding="utf-8")
    (gw_tests / "Gateway_Tests.cs").write_text(
        "namespace MTSSupplyChainGateway.UnitTests;\n"
        "public class GatewayTests {\n"
        f"    // {inv_pfx}-DOTNET-003\n"
        "    [TestCase(1)]\n"
        "    public void GatewayCase(int x) {}\n\n"
        f"    // {inv_pfx}-DOTNET-004\n"
        "    [TestMethod]\n"
        "    public void MsTestGateway() {}\n"
        "}\n",
        encoding="utf-8",
    )

    # 1. Verify .NET test classification, diff classification, and orphan test discovery
    assert is_polyglot_test_file("GBFSolution/GBF.Tests/PartyTests.cs", repo_root=repo) is True
    assert is_polyglot_test_file("GBFSolution/GBF.Tests/PartyHelper.cs", repo_root=repo, package_paths=["GBFSolution"]) is True
    assert is_polyglot_test_file("GBFSolution/MTSSupplyChainGateway.UnitTests/Gateway_Tests.cs", repo_root=repo) is True
    assert is_polyglot_test_file("GBFSolution/OtherFolder/OrderTest.cs", repo_root=repo) is True
    assert is_polyglot_test_file("GBFSolution/GBF.Service/PartyService.cs", repo_root=repo) is False

    collected = collect_polyglot_test_files(repo)
    assert "GBFSolution/GBF.Tests/PartyTests.cs" in collected
    assert "GBFSolution/MTSSupplyChainGateway.UnitTests/Gateway_Tests.cs" in collected
    assert not any("bin/" in k or "obj/" in k for k in collected)

    classified = classify_changed_files(
        [
            "GBFSolution/GBF.Tests/PartyTests.cs",
            "GBFSolution/MTSSupplyChainGateway.UnitTests/Gateway_Tests.cs",
            "GBFSolution/GBF.Service/PartyService.cs",
        ],
        repo_root=repo,
        eidos_dir=eidos_dir,
        package_paths=["GBFSolution/GBF.Model", "GBFSolution/GBF.Service"],
    )
    assert sorted(classified.test_files) == [
        "GBFSolution/GBF.Tests/PartyTests.cs",
        "GBFSolution/MTSSupplyChainGateway.UnitTests/Gateway_Tests.cs",
    ]
    assert classified.code_files == ["GBFSolution/GBF.Service/PartyService.cs"]

    # 2. Verify AST extraction for C# properties, fields, indexers, records, events, attributes, and namespaces
    ast_idx = get_ast_indexer()
    party_syms = {s.name: s for s in ast_idx.extract_symbols(gbf_model / "Party.cs")}
    assert _is_public_symbol(party_syms["DefaultCurrency"]) is True
    assert _is_public_symbol(party_syms["this"]) is True
    assert _is_public_symbol(party_syms["Name"]) is True
    assert party_syms["Name"].signature == '[JsonPropertyName("name")] public string Name'
    assert _is_public_symbol(party_syms["DisplayName"]) is True
    assert party_syms["DisplayName"].signature == "public string DisplayName"
    assert _is_public_symbol(party_syms["Updated"]) is True
    assert _is_public_symbol(party_syms["PartyId"]) is True
    assert _is_public_symbol(party_syms["InternalRevision"]) is False
    assert _is_component_surface_symbol(party_syms["InternalRevision"]) is True
    assert _is_public_symbol(party_syms["SecretToken"]) is False
    assert _is_component_surface_symbol(party_syms["SecretToken"]) is False
    assert _is_component_surface_symbol(party_syms["UnannotatedPrivate"]) is False
    assert _is_public_symbol(party_syms["SecretNested"]) is False

    svc_syms = {s.name: s for s in ast_idx.extract_symbols(gbf_svc / "PartyService.cs")}
    assert _is_public_symbol(svc_syms["PartyService"]) is True
    assert _is_public_symbol(svc_syms["CreateParty"]) is True
    assert _is_public_symbol(svc_syms["InternalSync"]) is False
    assert _is_component_surface_symbol(svc_syms["InternalSync"]) is True
    assert _is_component_surface_symbol(svc_syms["SecretAudit"]) is False
    assert ast_idx.extract_declared_namespaces(gbf_model / "BaseEntity.cs") == ["GBF.Model.Library"]

    # 3. Author specs covering containers, components, OpenAPI contract, and capabilities
    (eidos_dir / "system.adoc").write_text(
        "---\ntype: product/system\nid: SYS-GBF\ntitle: GBF Solution\nstatus: active\narchetypes: [service]\n---\n= GBF\n",
        encoding="utf-8",
    )
    (eidos_dir / "domains" / "billing" / "domain.adoc").write_text(
        "---\ntype: product/domain\nid: DOM-BILLING\ntitle: Billing\nstatus: active\nclassification: core\nowners: [Core]\n---\n= Billing\n",
        encoding="utf-8",
    )
    (cnt_dir / "model.adoc").write_text(
        "---\ntype: tech/container\nid: CNT-MODEL\ntitle: GBF Model\nstatus: active\ncontainer_type: library\nruntime: .NET 8\nsource_path: GBFSolution/GBF.Model\ndomain: DOM-BILLING\n---\n= Model\n\n"
        "== Public Surface\n"
        "* `Party.Id`\n"
        "* `Party.DefaultCurrency`\n"
        "* `Party.Name`\n"
        "* `Party.DisplayName`\n"
        "* `Party.this[int]`\n"
        "* `Party.Updated`\n"
        "* `PartyRecord.PartyId`\n",
        encoding="utf-8",
    )
    (cnt_dir / "service.adoc").write_text(
        "---\ntype: tech/container\nid: CNT-SERVICE\ntitle: GBF Service\nstatus: active\ncontainer_type: service\nruntime: .NET 8\nsource_path: GBFSolution/GBF.Service\ndomain: DOM-BILLING\n---\n= Service\n",
        encoding="utf-8",
    )
    cmp_party = cmp_dir / "party.adoc"
    cmp_party.write_text(
        "---\ntype: tech/component\nid: CMP-GBF-PARTY\ntitle: Party Model\ncontainer: CNT-MODEL\nstatus: active\nsource_path: GBFSolution/GBF.Model\n---\n= Party\n\n"
        "== Public Surface\n"
        "* `Party.Id` — Inherited base entity key via `SOAP` / `POST` wrappers (`Startup.cs`, `Customs.dll`, `Service.svc`, `Report.rdlc`, `Schema.wsdl`, `Default.aspx`, `Tool.exe`).\n"
        "* `Party.DefaultCurrency`\n"
        "* `Party.Name`\n"
        "* `Party.DisplayName`\n"
        "* `Party.this[int]`\n"
        "* `Party.InternalRevision`\n"
        "* `PartyRecord.PartyId`\n\n"
        "Mentions `SOAP`, `POST`, and `BinArtifactOnly` in prose narrative without failing.\n",
        encoding="utf-8",
    )
    (cmp_dir / "party-service.adoc").write_text(
        "---\ntype: tech/component\nid: CMP-GBF-SERVICE\ntitle: Party Service\ncontainer: CNT-SERVICE\nstatus: active\nsource_path: GBFSolution/GBF.Service\n---\n= PartyService\n\n"
        "== Public Surface\n"
        "* `PartyService.CreateParty()`\n"
        "* `PartyService.InternalSync()`\n",
        encoding="utf-8",
    )
    (cmp_dir / "rust-store.adoc").write_text(
        "---\ntype: tech/component\nid: CMP-RUST-STORE\ntitle: Rust Store\ncontainer: CNT-MODEL\nstatus: active\nsource_path: src/rust_store/lib.rs\n---\n= Rust Store\n\n"
        "== Public Surface\n"
        "* `SubStore.super_op()`\n"
        "* `SubStore.sub_op()`\n",
        encoding="utf-8",
    )
    (repo / "openapi.yaml").write_text(
        "openapi: 3.0.0\npaths:\n  /api/party:\n    post: {}\n",
        encoding="utf-8",
    )
    ctr_api = ctr_dir / "party-api.adoc"
    ctr_api.write_text(
        "---\ntype: tech/contract\nid: CTR-PARTY-API\ntitle: Party API\nstatus: active\nprotocol: openapi\ncontainer: CNT-SERVICE\nspecification_file: openapi.yaml\n---\n= Party API\n\n"
        "== Architectural Boundary\n"
        "Unlike legacy `POST /some/other/route`, this service delegates batch exports to an external gateway.\n\n"
        "== Endpoints\n"
        "* `POST /api/party` — Unlike legacy `POST /other/contrast/route`, creates a party.\n",
        encoding="utf-8",
    )
    (dom_dir / "cap-gbf.adoc").write_text(
        "---\n"
        "type: product/capability\n"
        "id: CAP-GBF-001\n"
        "title: Party Validation\n"
        "domain: DOM-BILLING\n"
        "status: active\n"
        "technical:\n"
        "  containers: [CNT-MODEL, CNT-SERVICE]\n"
        "  components: [CMP-GBF-PARTY, CMP-GBF-SERVICE]\n"
        "  contracts: [CTR-PARTY-API]\n"
        "  invariants:\n"
        f"    - id: {inv_pfx}-DOTNET-001\n"
        "      rule: Fact DisplayName\n"
        "      test_binding: GBFSolution/GBF.Tests/PartyTests.cs::PartyTests::ValidatesParty\n"
        f"    - id: {inv_pfx}-DOTNET-002\n"
        "      rule: Theory\n"
        "      test_binding: GBFSolution/GBF.Tests/PartyTests.cs::ValidatesPartyCode\n"
        f"    - id: {inv_pfx}-DOTNET-003\n"
        "      rule: NUnit TestCase\n"
        "      test_binding: GBFSolution/MTSSupplyChainGateway.UnitTests/Gateway_Tests.cs::GatewayTests::GatewayCase\n"
        f"    - id: {inv_pfx}-DOTNET-004\n"
        "      rule: MSTest TestMethod\n"
        "      test_binding: GBFSolution/MTSSupplyChainGateway.UnitTests/Gateway_Tests.cs::MsTestGateway\n"
        "---\n= Cap\n",
        encoding="utf-8",
    )

    validator = SpecValidator()
    rep = validator.validate_directory(eidos_dir)
    assert rep.is_clean, f"Expected clean validation, got: {rep.broken_references} | {rep.schema_errors}"

    # Verify strict visibility rejections + OpenAPI drift on Endpoints
    cnt_model_path = cnt_dir / "model.adoc"
    orig_cnt_model = cnt_model_path.read_text(encoding="utf-8")
    cnt_model_path.write_text(orig_cnt_model + "* `Party.InternalRevision`\n", encoding="utf-8")
    rep_cnt_internal = validator.validate_directory(eidos_dir)
    assert not rep_cnt_internal.is_clean
    assert any("Party.InternalRevision" in err for err in rep_cnt_internal.broken_references)
    cnt_model_path.write_text(orig_cnt_model, encoding="utf-8")

    orig_cmp_party = cmp_party.read_text(encoding="utf-8")
    cmp_party.write_text(
        orig_cmp_party
        + "* `Party.SecretToken`\n* `Party.UnannotatedPrivate()`\n* `PrivateNested`\n* `PrivateNested.SecretNested()`\n* `BinArtifactOnly`\n* `Party.MissingMember`\n",
        encoding="utf-8",
    )
    rep_cmp_bad = validator.validate_directory(eidos_dir)
    joined_bad = "\n".join(rep_cmp_bad.broken_references)
    assert "Party.SecretToken" in joined_bad
    assert "Party.UnannotatedPrivate" in joined_bad
    assert "PrivateNested" in joined_bad
    assert "PrivateNested.SecretNested" in joined_bad
    assert "BinArtifactOnly" in joined_bad
    assert "Party.MissingMember" in joined_bad
    cmp_party.write_text(orig_cmp_party, encoding="utf-8")

    orig_ctr = ctr_api.read_text(encoding="utf-8")
    ctr_api.write_text(orig_ctr + "* `DELETE /api/phantom`\n", encoding="utf-8")
    rep_ctr_bad = validator.validate_directory(eidos_dir)
    assert any("/api/phantom" in err for err in rep_ctr_bad.broken_references)
    ctr_api.write_text(orig_ctr, encoding="utf-8")

    # 4. Verify index sync: .NET test self-healing, skip attributes, namespace + .csproj edges
    db_path = eidos_dir / ".cache" / "index.db"
    idx = ArchitecturalIndex(db_path=db_path, repo_root=repo, eidos_dir=eidos_dir)
    try:
        conn = idx._get_connection()
        # Seed stale misclassified row to verify self-healing without --force
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO indexed_files (rel_path, mtime, sha256, doc_id, doc_type) VALUES (?, 1.0, 'stale', 'CNT-MODEL', 'code/source');",
                ("GBFSolution/GBF.Tests/PartyTests.cs",),
            )
        idx.sync(force=False, auto_semantic=False)

        test_docs = {
            r[0]: r[1]
            for r in conn.execute("SELECT rel_path, doc_type FROM indexed_files WHERE rel_path LIKE '%Tests.cs';").fetchall()
        }
        assert test_docs["GBFSolution/GBF.Tests/PartyTests.cs"] == "code/test"
        assert test_docs["GBFSolution/MTSSupplyChainGateway.UnitTests/Gateway_Tests.cs"] == "code/test"
        assert conn.execute("SELECT COUNT(*) FROM code_symbols WHERE is_test = 1;").fetchone()[0] >= 4

        assert_invs = {r[0] for r in conn.execute("SELECT DISTINCT target_id FROM edges WHERE rel_type = 'asserts';").fetchall()}
        for req_inv in (f"{inv_pfx}-DOTNET-001", f"{inv_pfx}-DOTNET-002", f"{inv_pfx}-DOTNET-003", f"{inv_pfx}-DOTNET-004"):
            assert req_inv in assert_invs
        assert f"{inv_pfx}-DOTNET-SKIP" not in assert_invs
        assert f"{inv_pfx}-DOTNET-IGNORE" not in assert_invs

        edges = {(r[0], r[1], r[2]) for r in conn.execute("SELECT source_id, target_id, rel_type FROM edges;").fetchall()}
        assert ("CMP-GBF-SERVICE", "CMP-GBF-PARTY", "consumes_api") in edges
        assert ("CNT-SERVICE", "CNT-MODEL", "depends_on") in edges
        assert ("CMP-GBF-PARTY", "CMP-GBF-SERVICE", "consumes_api") not in edges
        assert ("CNT-MODEL", "CNT-SERVICE", "depends_on") not in edges

        # Ensure index_source_file produces clean namespace symbols and no false self-edges on shared namespaces
        assert not any(s == t for s, t, rt in edges if rt in ("consumes_api", "depends_on"))
    finally:
        idx.close()

    # 5. Verify 8,192-entry mtime_ns LRU cache holds >512 files without wiping or re-reading
    SpecValidator._PUBLIC_SURFACE_SYMBOL_CACHE.clear()
    many_dir = repo / "ManyCs"
    many_dir.mkdir()
    for i in range(550):
        (many_dir / f"Item{i}.cs").write_text(f"public class Item{i} {{ public int Val{i} {{ get; set; }} }}\n", encoding="utf-8")
    (cnt_dir / "many.adoc").write_text(
        "---\ntype: tech/container\nid: CNT-MANY\ntitle: Many\nstatus: active\ncontainer_type: library\nruntime: .NET\nsource_path: ManyCs\ndomain: DOM-BILLING\n---\n= Many\n\n== Public Surface\n* `Item0.Val0`\n* `Item549.Val549`\n",
        encoding="utf-8",
    )
    rep_many_1 = validator.validate_directory(eidos_dir)
    assert rep_many_1.is_clean, rep_many_1.broken_references
    assert len(SpecValidator._PUBLIC_SURFACE_SYMBOL_CACHE) >= 550
    cache_keys = list(SpecValidator._PUBLIC_SURFACE_SYMBOL_CACHE.keys())
    assert all(isinstance(k[1], int) and isinstance(k[2], int) for k in cache_keys)
    rep_many_2 = validator.validate_directory(eidos_dir)
    assert rep_many_2.is_clean


def test_orphan_invariants_ignores_commented_out_csharp_blocks_and_invoice_numbers(tmp_path: Path) -> None:
    """Verify INV-DRIFT-DETECTION-ACCURACY: commented-out C# blocks, #if false sections, and pure-numeric invoice IDs do not cause false orphan reports while stale v1 test_binding_cache rows auto-invalidate."""
    inv_pfx = "INV"
    repo = tmp_path / "orphan_csharp_repo"
    eidos_dir = repo / "eidos"
    dom_dir = eidos_dir / "domains" / "billing" / "capabilities"
    tests_dir = repo / "GBFSolution" / "GBF.Tests"
    dom_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)

    cs_rel = "GBFSolution/GBF.Tests/RemoteDocumentAttachmentService_Tests.cs"
    cs_path = repo / cs_rel
    cs_content = "\n".join([
        "using Xunit;",
        "public class RemoteDocumentAttachmentService_Tests",
        "{",
        "    /*",
        "    [Fact]",
        "    public async Task DeadAttachmentTest()",
        "    {",
        "        var payload = new Dictionary<string, string>",
        "        {",
        f'            {{ "UnifiedInvoiceNumber", "{inv_pfx}-2026-000123" }},',
        f'            {{ "LegacyRef", "{inv_pfx}-LEGACY-COMMENTED-001" }}',
        "        };",
        "    }",
        "    */",
        "",
        "#if false",
        "    [Fact]",
        "    public void DisabledByPreproc()",
        "    {",
        f'        var s = "{inv_pfx}-PREPROC-001";',
        "    }",
        "#elif false",
        f'    var unused = "{inv_pfx}-PREPROC-ELIF";',
        "#else",
        "    // active else arm has no orphan",
        "#endif",
        "",
        "    /*",
        "    [Fact]",
        "    public void PrecedingDeadTest() { }",
        "    */",
        f"    /// <summary>{inv_pfx}-XML-BOUND-001</summary>",
        f"    // {inv_pfx}-REAL-BOUND-001",
        "    [Fact]",
        "    public void Should_Verify_Active_With_Globs()",
        "    {",
        f'        var liveInvoice = "{inv_pfx}-2026-999888";',
        '        var globs = new[] { "apps/*/Dockerfile", "libs/*/Cargo.toml" };',
        '        var verb = $@"C:\\{liveInvoice}\\/*not-comment*/";',
        '        var raw = """/*not-comment*/""";',
        f'        // var oldId = "{inv_pfx}-LINE-COMMENTED-001";',
        f"        // eidos:fixture var kept = \"{inv_pfx}-FIXTURE-IN-COMMENT\";",
        f'        var realOrphan = "{inv_pfx}-GENUINE-ORPHAN-001";',
        "    }",
        "}",
        "",
    ])
    cs_path.write_text(cs_content, encoding="utf-8")

    # Also exercise polyglot helper edge cases for high coverage and precision
    js_stripped = _strip_commented_out_code_blocks(
        "\n".join([
            "/** @param {string} x " + f"{inv_pfx}-JSDOC-BOUND */",
            "/* " + f"{inv_pfx}-PROSE-ONLY" + " */",
            "const a = '/*'; const b = `/*\\`*/`; // let c = 'ignored';",
            "/* { x = \"" + f"{inv_pfx}-DEAD-JS" + "\"; } */",
        ]),
        "sample.test.ts",
    )
    assert f"{inv_pfx}-JSDOC-BOUND" in js_stripped
    assert f"{inv_pfx}-PROSE-ONLY" in js_stripped
    assert "const a = '/*'" in js_stripped
    assert "const b = `/*\\`*/`" in js_stripped
    assert f"{inv_pfx}-DEAD-JS" not in js_stripped

    go_stripped = _strip_commented_out_code_blocks('const p = `/*\\` + "*/";', "a_test.go")
    assert "`/*\\`" in go_stripped
    rs_stripped = _strip_commented_out_code_blocks(
        "fn f<'a>() { let _c = 'x'; /* { let _z = \"" + f"{inv_pfx}-DEAD-RS" + "\"; } */ }",
        "a_test.rs",
    )
    assert "fn f<'a>()" in rs_stripped
    assert f"{inv_pfx}-DEAD-RS" not in rs_stripped

    cap_path = dom_dir / "attachments.adoc"
    cap_path.write_text(
        "\n".join([
            "---",
            "type: product/capability",
            "id: CAP-BILLING-001",
            "title: Attachments",
            "domain: DOM-BILLING",
            "status: active",
            'version: "1.0.0"',
            "technical:",
            "  invariants:",
            f"    - id: {inv_pfx}-REAL-BOUND-001",
            "      rule: Active attachment test verifies valid glob paths.",
            f"      test_binding: {cs_rel}::RemoteDocumentAttachmentService_Tests::Should_Verify_Active_With_Globs",
            f"    - id: {inv_pfx}-XML-BOUND-001",
            "      rule: XML doc comment invariant binding remains intact.",
            f"      test_binding: {cs_rel}",
            "---",
            "= Attachments",
            "",
        ]),
        encoding="utf-8",
    )

    known_ids = {f"{inv_pfx}-REAL-BOUND-001", f"{inv_pfx}-XML-BOUND-001"}

    # 1. Verify extract_invariants_and_verify and raw-string _verify_single_invariant
    traces = extract_invariants_and_verify(eidos_dir, repo)
    assert len(traces) == 2
    assert all(t.has_test_coverage for t in traces), f"Uncovered invariants: {traces}"
    assert _verify_single_invariant(f"{inv_pfx}-XML-BOUND-001", cs_rel, repo, {cs_rel: cs_content}, {}) == [cs_rel]
    assert _verify_single_invariant(f"{inv_pfx}-LEGACY-COMMENTED-001", cs_rel, repo, {cs_rel: cs_content}, {}) == []

    # 2. Verify uncached detect_orphan_invariants flags only the genuine orphan token
    uncached_orphans = detect_orphan_invariants(
        repo,
        known_ids,
        {cs_rel: cs_content},
        eidos_dir=eidos_dir,
    )
    assert uncached_orphans == [
        (f"{inv_pfx}-GENUINE-ORPHAN-001", f"{cs_rel}::RemoteDocumentAttachmentService_Tests::Should_Verify_Active_With_Globs")
    ], f"Unexpected uncached orphans: {uncached_orphans}"

    # 3. Seed stale v1 test_binding_cache row matching mtime_ns + sha256, verify auto-invalidation
    db_path = eidos_dir / ".cache" / "index.db"
    stat = cs_path.stat()
    sha = hashlib.sha256(cs_content.encode("utf-8")).hexdigest()
    stale_funcs = orjson.dumps(
        {
            "RemoteDocumentAttachmentService_Tests::DeadAttachmentTest": [
                f"{inv_pfx}-2026-000123",
                f"{inv_pfx}-LEGACY-COMMENTED-001",
            ]
        }
    ).decode("utf-8")
    save_test_binding_cache(db_path, [(cs_rel, stat.st_mtime_ns, sha, stale_funcs, "[]")])
    with contextlib.closing(open_cache_connection(db_path)) as conn, conn:
        conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('test_binding_cache_ver', '1');")

    assert load_test_binding_cache(db_path) == {}
    with contextlib.closing(open_cache_connection(db_path)) as conn:
        ver_row = conn.execute("SELECT value FROM metadata WHERE key = 'test_binding_cache_ver';").fetchone()
        assert ver_row is not None and ver_row[0] == "2"

    # 4. Run cached detect_orphan_invariants (all_tests_content=None) twice to verify cache rebuild + cache hit
    cached_orphans_1 = detect_orphan_invariants(
        repo,
        known_ids,
        None,
        eidos_dir=eidos_dir,
    )
    assert cached_orphans_1 == [
        (f"{inv_pfx}-GENUINE-ORPHAN-001", f"{cs_rel}::RemoteDocumentAttachmentService_Tests::Should_Verify_Active_With_Globs")
    ], f"Unexpected cached orphans: {cached_orphans_1}"

    cached_rows = load_test_binding_cache(db_path)
    assert cs_rel in cached_rows
    cached_orphans_2 = detect_orphan_invariants(
        repo,
        known_ids,
        None,
        eidos_dir=eidos_dir,
    )
    assert cached_orphans_2 == cached_orphans_1
