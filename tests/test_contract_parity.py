"""Tests for modern rest_http / http_json bidirectional route and ASP.NET Core auth contract parity (DLT-2026-142)."""

from pathlib import Path

from eidos.core.index.ast_http import extract_http_endpoints_with_metadata
from eidos.core.index.ast_indexer import get_ast_indexer
from eidos.core.index.graph_index import ArchitecturalIndex
from eidos.core.index.http_linker import _derive_http_request_edges, _extract_contract_http_routes
from eidos.core.spec.validator import SpecValidator


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.lstrip("\n"), encoding="utf-8")
    return path


def _scaffold_minimal_repo(tmp_path: Path, *, contract_body: str, protocol: str, spec_file: str) -> Path:
    eidos_dir = tmp_path / "eidos"
    _write(
        eidos_dir / "system.adoc",
        """
---
type: product/system
id: SYS-DEMO
title: "Demo System"
status: active
archetypes:
  - api
---
= Demo System
""",
    )
    _write(
        eidos_dir / "domains" / "orders" / "domain.adoc",
        """
---
type: product/domain
id: DOM-ORDERS
title: "Orders"
status: active
classification: core
---
= Orders
""",
    )
    _write(
        eidos_dir / "architecture" / "containers" / "api.adoc",
        """
---
type: tech/container
id: CNT-API
title: "API Service"
status: active
container_type: service
runtime: "ASP.NET Core 9"
domain: DOM-ORDERS
source_path: "src/Api"
contracts:
  provides:
    - CTR-API-HTTP
---
= API Service
""",
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "api-http.adoc",
        f"""
---
type: tech/contract
id: CTR-API-HTTP
title: "Orders HTTP Contract"
status: active
protocol: "{protocol}"
container: CNT-API
specification_file: "{spec_file}"
---
{contract_body}
""",
    )
    return eidos_dir


def test_rest_http_bidirectional_route_and_authorize_parity(tmp_path: Path) -> None:
    """Verify INV-CONTRACT-REST-HTTP-PARITY across ASP.NET Core controllers, Minimal APIs, globs, dirs, auth drift, and zero-route fallback."""
    # 1. Clean ASP.NET Core controller + Minimal API via brace-glob specification_file
    #    Covers: [action] token, leading '/' and '~/' overrides, [Route] above [HttpGet],
    #    controller/action [Authorize(Roles/Policy)] inheritance, [AllowAnonymous] override,
    #    Minimal API MapGroup.RequireAuthorization() + .AllowAnonymous().
    _write(
        tmp_path / "src" / "Api" / "Controllers" / "OrdersController.cs",
        """
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace Api.Controllers;

[ApiController]
[Route("api/[controller]")]
[Authorize(Roles = "User")]
public class OrdersController : ControllerBase
{
    [HttpGet("{id:guid}")]
    public IActionResult GetById(Guid id) => Ok();

    [Route("export/{id}")]
    [HttpGet]
    [Authorize(Policy = "CanExport")]
    public IActionResult Export(int id) => Ok();

    [HttpGet("/health")]
    [AllowAnonymous]
    public IActionResult Health() => Ok();

    [HttpPost("~/webhooks/orders")]
    [AllowAnonymous]
    public IActionResult Webhook() => Ok();

    [Route("[action]")]
    [HttpGet]
    [Authorize(Roles = "Admin")]
    public IActionResult Summary() => Ok();
}

[Route("api/[controller]/[action]")]
public class ReportsController : ControllerBase
{
    [HttpGet]
    public IActionResult Daily() => Ok();
}
""",
    )
    _write(
        tmp_path / "src" / "Api" / "Endpoints" / "BillingEndpoints.cs",
        """
namespace Api.Endpoints;

public static class BillingEndpoints
{
    public static void Map(WebApplication app)
    {
        var billing = app.MapGroup("/api/billing").RequireAuthorization("BillingPolicy");
        billing.MapGet("/invoices/{id}", (int id) => Results.Ok());
        billing.MapPost("/invoices", () => Results.Ok()).RequireAuthorization("BillingWrite");
        billing.MapGet("/status", () => Results.Ok()).AllowAnonymous();
    }
}
""",
    )
    # Also put a file under bin/ to ensure directory walking prunes build output.
    _write(
        tmp_path / "src" / "Api" / "bin" / "GeneratedController.cs",
        """
public class GeneratedController {
    [HttpGet("/should-be-ignored")]
    public void Ignored() {}
}
""",
    )

    clean_body = """
= Orders HTTP Contract

== Architectural Boundary
Unlike `DELETE /api/legacy/orders`, this contract governs only modern ASP.NET Core routes.

== Endpoints

[cols="1,2,2,2", options="header"]
|===
| Method | Path | Auth | Description
| `GET` | `/api/orders/{id}` | `[Authorize(Roles = "User")]` | Fetch order by ID.
| `GET` | `/api/orders/export/{id}` | `[Authorize(Policy = "CanExport", Roles = "User")]` | Export order.
| `GET` | `/health` | `[AllowAnonymous]` | Health check root override.
| `POST` | `/webhooks/orders` | `Anonymous` | Inbound webhook tilde override.
| `GET` | `/api/orders/summary` | `[Authorize(Roles = "Admin")]` | Action token summary.
| `GET` | `/api/reports/daily` | | Controller-level action token report.
| `GET /api/billing/invoices/{id}` | | `[Authorize("BillingPolicy")]` | Fetch invoice.
| `POST` | `/api/billing/invoices` | `RequireAuthorization("BillingPolicy", "BillingWrite")` | Create invoice.
| `GET` | `/api/billing/status` | `[AllowAnonymous]` | Public billing status.
|===
"""
    eidos_dir = _scaffold_minimal_repo(
        tmp_path,
        contract_body=clean_body,
        protocol="rest_http",
        spec_file="src/Api/{Controllers,Endpoints}/*.cs",
    )
    validator = SpecValidator()
    report = validator.validate_directory(eidos_dir, check_import_boundaries=False)
    assert report.is_clean, (
        f"Expected clean rest_http parity, got schema={report.schema_errors} refs={report.broken_references}"
    )

    # 2. Directory specification_file works and skips bin/
    contract_path = eidos_dir / "architecture" / "contracts" / "api-http.adoc"
    contract_path.write_text(
        contract_path.read_text(encoding="utf-8").replace(
            'specification_file: "src/Api/{Controllers,Endpoints}/*.cs"',
            'specification_file: "src/Api"',
        ),
        encoding="utf-8",
    )
    dir_report = validator.validate_directory(eidos_dir, check_import_boundaries=False)
    assert dir_report.is_clean, f"Directory spec_file failed: {dir_report.broken_references}"

    # 3. Missing route, phantom route, and [Authorize]/Roles/Policy/[AllowAnonymous] contradictions are all caught.
    drifted_body = """
= Orders HTTP Contract

== Endpoints

[cols="1,2,2", options="header"]
|===
| Method | Path | Auth
| `GET` | `/api/orders/{id}` | `[AllowAnonymous]`
| `GET` | `/api/orders/export/{id}` | `[Authorize(Policy = "WrongPolicy", Roles = "SuperAdmin")]`
| `GET` | `/health` | `[Authorize]`
| `POST` | `/webhooks/orders` | `Anonymous`
| `GET` | `/api/orders/summary` | `[Authorize]`
| `GET` | `/api/reports/daily` | `Required`
| `GET` | `/api/billing/invoices/{id}` | `[Authorize]`
| `POST` | `/api/billing/invoices` | `[Authorize]`
| `DELETE` | `/api/orders/phantom` | `[Authorize]`
|===
"""
    contract_path.write_text(
        f"""---
type: tech/contract
id: CTR-API-HTTP
title: "Orders HTTP Contract"
status: active
protocol: "rest_http"
container: CNT-API
specification_file: "src/Api"
---
{drifted_body.lstrip()}""",
        encoding="utf-8",
    )
    drift_report = validator.validate_directory(eidos_dir, check_import_boundaries=False)
    refs = "\n".join(drift_report.broken_references)
    assert "GET /api/billing/status" in refs and "is not documented" in refs
    assert "DELETE /api/orders/phantom" in refs and "does not exist" in refs
    assert "documents [AllowAnonymous]" in refs and "/api/orders/{*}" in refs
    assert 'Policy = "WrongPolicy"' in refs
    assert 'Roles = "SuperAdmin"' in refs
    assert "documents [Authorize]" in refs and "/health" in refs

    # 4. Plain route table without an Auth column still enforces route parity cleanly (opt-in auth).
    plain_table_body = """
= Orders HTTP Contract

== Endpoints
* `GET /api/orders/{id}`
* `GET /api/orders/export/{id}`
* `GET /health` (unlike `GET /ignored/contrast` which is excluded)
* `POST /webhooks/orders`
* `GET /api/orders/summary`
* `GET /api/reports/daily`
* `GET /api/billing/invoices/{id}`
* `POST /api/billing/invoices`
* `GET /api/billing/status`
"""
    contract_path.write_text(
        f"""---
type: tech/contract
id: CTR-API-HTTP
title: "Orders HTTP Contract"
status: active
protocol: "rest_http"
container: CNT-API
specification_file: "src/Api"
---
{plain_table_body.lstrip()}""",
        encoding="utf-8",
    )
    plain_report = validator.validate_directory(eidos_dir, check_import_boundaries=False)
    assert plain_report.is_clean, f"Plain route contract should pass opt-in auth: {plain_report.broken_references}"

    # 5. Zero-route fallback: stdlib BaseHTTPRequestHandler (0 framework endpoints) must not emit phantom route errors.
    _write(
        tmp_path / "src" / "Api" / "custom_server.py",
        """
from http.server import BaseHTTPRequestHandler

class CustomHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/custom":
            self.send_response(200)
""",
    )
    contract_path.write_text(
        """---
type: tech/contract
id: CTR-API-HTTP
title: "Custom HTTP Contract"
status: active
protocol: "rest_http"
container: CNT-API
specification_file: "src/Api/custom_server.py"
---
= Custom HTTP Contract

== Endpoints

|===
| Method | Path
| `GET` | `/api/custom`
|===
""",
        encoding="utf-8",
    )
    custom_report = validator.validate_directory(eidos_dir, check_import_boundaries=False)
    assert custom_report.is_clean, f"Zero-route fallback failed: {custom_report.broken_references}"

    # 6. Legacy SOAP/WCF and Web API 2 syntax are intentionally ignored (0 extracted endpoints).
    legacy_cs = _write(
        tmp_path / "src" / "Api" / "LegacyService.cs",
        """
[ServiceContract]
public interface ILegacySoap {
    [OperationContract]
    string Ping();
}

[RoutePrefix("api/legacy")]
public class LegacyApiController {
    [AcceptVerbs("GET")]
    public void GetOld() {}
}
""",
    )
    indexer = get_ast_indexer()
    assert extract_http_endpoints_with_metadata(indexer, legacy_cs, legacy_cs.read_text(encoding="utf-8")) == []


def test_rest_http_polyglot_fastapi_parity_and_http_linker_integration(tmp_path: Path) -> None:
    """Verify polyglot FastAPI rest_http parity and http_linker rest_http contract edge linking."""
    _write(
        tmp_path / "src" / "Api" / "app.py",
        """
from fastapi import FastAPI

app = FastAPI()

@app.get("/api/items/{item_id}")
def get_item(item_id: str):
    return {"id": item_id}

@app.post("/api/items")
def create_item():
    return {}
""",
    )
    _write(
        tmp_path / "src" / "Web" / "client.ts",
        """
export async function loadItem(id: string) {
  return fetch(`/api/items/${id}`);
}
""",
    )
    eidos_dir = _scaffold_minimal_repo(
        tmp_path,
        contract_body="""
= Items HTTP Contract

== Endpoints

|===
| Method | Path | Description
| `GET` | `/api/items/{item_id}` | Fetch item
| `POST` | `/api/items` | Create item
|===
""",
        protocol="rest_http",
        spec_file="src/Api/app.py",
    )
    _write(
        eidos_dir / "architecture" / "containers" / "web.adoc",
        """
---
type: tech/container
id: CNT-WEB
title: "Web Frontend"
status: active
container_type: web_app
runtime: "TypeScript"
domain: DOM-ORDERS
source_path: "src/Web"
---
= Web Frontend
""",
    )
    validator = SpecValidator()
    report = validator.validate_directory(eidos_dir, check_import_boundaries=False)
    assert report.is_clean, f"FastAPI rest_http parity failed: {report.broken_references}"

    # Verify http_linker recognizes protocol: rest_http and links CNT-WEB -> CTR-API-HTTP.
    idx = ArchitecturalIndex(repo_root=tmp_path, eidos_dir=eidos_dir)
    try:
        idx.sync(force=True, auto_semantic=False)
        conn = idx._get_connection()
        rows = conn.execute(
            "SELECT source_id, target_id, rel_type, provenance FROM edges WHERE provenance = 'ast_http';"
        ).fetchall()
        # Also verify _derive_http_request_edges derives repo_root from db_path when omitted.
        _derive_http_request_edges(conn)
        assert _extract_contract_http_routes(conn, None) == {}
    finally:
        idx.close()
    edge_set = {(r["source_id"], r["target_id"], r["rel_type"]) for r in rows}
    assert ("CNT-WEB", "CTR-API-HTTP", "consumes") in edge_set


def test_aspnet_core_attribute_edge_cases_and_minimal_api_inheritance() -> None:
    """Exercise ASP.NET Core comma-combined brackets, Template=, AllowAnonymous controller, nested MapGroup, and missing targets."""
    indexer = get_ast_indexer()
    cs_code = r"""
[Route(Template = "api/[controller]"), AllowAnonymous]
public class PublicController : ControllerBase
{
    private const string Label = "escaped \"quote\"";

    [Display(Name = "a\"b"), HttpGet(Template = "ping"), Authorize("PingPolicy")]
    public IActionResult Ping() => Ok();

    [HttpGet("open")]
    public IActionResult Open() => Ok();
}

[Route("api/v2/[controller]")]
[Authorize(Roles = "Ops, Support")]
public class OpsController : ControllerBase
{
    [method: HttpDelete("jobs/{id}")]
    public IActionResult DeleteJob(int id) => Ok();
}

public static class Program {
    public static void Main(WebApplication app) {
        var root = app.MapGroup("/v1").RequireAuthorization("RootPolicy");
        var child = root.MapGroup("/admin").WithTags("Admin").RequireAuthorization("AdminPolicy");
        child.MapGet("/metrics", () => Results.Ok()).WithName("GetMetrics");
        var pub = root.MapGroup("/public").AllowAnonymous();
        pub.MapGet("/info", () => Results.Ok());
        var dynGroup = app.MapGroup(prefixVar);
        dynGroup.MapGet(routeVar, () => Results.Ok());
    }
}
"""
    eps = extract_http_endpoints_with_metadata(indexer, Path("Controllers.cs"), cs_code)
    by_key = {(ep.method, ep.path): ep for ep in eps}
    assert by_key[("GET", "/api/public/ping")].auth_mode == "authorize"
    assert by_key[("GET", "/api/public/ping")].auth_policies == ("PingPolicy",)
    assert by_key[("GET", "/api/public/open")].auth_mode == "allow_anonymous"
    assert by_key[("DELETE", "/api/v2/ops/jobs/{*}")].auth_roles == ("Ops", "Support")
    assert by_key[("GET", "/v1/admin/metrics")].auth_mode == "authorize"
    assert by_key[("GET", "/v1/admin/metrics")].auth_policies == ("RootPolicy", "AdminPolicy")
    assert by_key[("GET", "/v1/public/info")].auth_mode == "allow_anonymous"
    assert extract_http_endpoints_with_metadata(indexer, Path("Empty.cs"), "") == []
    assert extract_http_endpoints_with_metadata(indexer, Path("NoRoutes.cs"), "public class Foo {}") == []
    assert extract_http_endpoints_with_metadata(indexer, Path("Unclosed.cs"), '[HttpGet("a"') == []


def test_overlapping_component_source_paths_and_base_controller_inheritance(tmp_path: Path) -> None:
    """Verify INV-OVERLAPPING-SOURCE-PATH-AND-BASE-CONTROLLER-PARITY: overlapping CMP-*.source_path warnings and cross-file ASP.NET Core base controller inheritance."""
    from eidos.core.index.ast_http import collect_aspnet_core_controller_bases

    ws = tmp_path / "overlap_and_base_ws"
    eidos_dir = ws / "eidos"
    (eidos_dir / "architecture" / "containers").mkdir(parents=True)
    (eidos_dir / "architecture" / "components").mkdir(parents=True)
    (eidos_dir / "architecture" / "contracts").mkdir(parents=True)

    # 1. Shared directory with 4 code files claimed by two active components -> single deduplicated warning with (+1 more)
    for fname in ("a.py", "b.py", "c.py", "d.py"):
        _write(ws / "src" / "shared" / fname, f"def fn_{fname[0]}() -> None:\n    pass\n")
    _write(ws / "src" / "other" / "x.py", "def fn_x() -> None:\n    pass\n")

    # 2. Cross-file ASP.NET Core base controller hierarchy (BaseApiController -> TenantApiController -> OrdersController)
    base_cs = """
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

[ApiController]
[Route("api/v1/[controller]")]
[Authorize(Policy = "ApiUser")]
public abstract class BaseApiController<T> : ControllerBase where T : class {}
"""
    mid_cs = """
public abstract class TenantApiController : BaseApiController<string> {}
"""
    orders_cs = """
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

public class OrdersController : TenantApiController
{
    [HttpGet("{id}")]
    public IActionResult GetById(int id) => Ok();

    [HttpGet("health")]
    [AllowAnonymous]
    public IActionResult Health() => Ok();

    [HttpPost]
    [Authorize(Roles = "Admin")]
    public IActionResult Create() => Ok();
}
"""
    public_derived_cs = """
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

[AllowAnonymous]
public class StatusController : BaseApiController<object>
{
    [HttpGet("ping")]
    public IActionResult Ping() => Ok();
}
"""
    _write(ws / "src" / "Api" / "BaseApiController.cs", base_cs)
    _write(ws / "src" / "Api" / "TenantApiController.cs", mid_cs)
    _write(ws / "src" / "Api" / "OrdersController.cs", orders_cs)
    _write(ws / "src" / "Api" / "StatusController.cs", public_derived_cs)

    # Unit check partial class merging in collect_aspnet_core_controller_bases
    merged_bases = collect_aspnet_core_controller_bases(
        [
            (Path("A.cs"), '[Route("api/[controller]")] public partial class PartialCtrl : BaseA {}'),
            (Path("B.cs"), '[Authorize(Policy = "P1")] public partial class PartialCtrl : IMarker {}'),
            (Path("C.cs"), "[AllowAnonymous] public partial class AnonCtrl : PartialCtrl {}"),
            (Path("D.cs"), "[Authorize] public partial class AnonCtrl {}"),
        ]
    )
    assert merged_bases["PartialCtrl"]["route_template"] == "api/[controller]"
    assert merged_bases["PartialCtrl"]["auth_policies"] == ("P1",)
    assert merged_bases["AnonCtrl"]["auth_mode"] == "allow_anonymous"

    _write(
        eidos_dir / "architecture" / "containers" / "api.adoc",
        "---\ntype: tech/container\nid: CNT-API\ntitle: API\nstatus: active\n"
        "container_type: service\nruntime: .NET 9\nsource_path:\n  - src/shared\n  - src/other\n  - src/Api\n---\n= API\n",
    )
    _write(
        eidos_dir / "architecture" / "components" / "comp-a.adoc",
        "---\ntype: tech/component\nid: CMP-ALPHA\ntitle: Alpha\nstatus: active\n"
        "container: CNT-API\nsource_path: src/shared\n---\n= Alpha\n",
    )
    _write(
        eidos_dir / "architecture" / "components" / "comp-b.adoc",
        "---\ntype: tech/component\nid: CMP-BETA\ntitle: Beta\nstatus: active\n"
        "container: CNT-API\nsource_path:\n  - src/shared\n  - src/other\n---\n= Beta\n",
    )
    contract_path = eidos_dir / "architecture" / "contracts" / "api-http.adoc"
    _write(
        contract_path,
        """---
type: tech/contract
id: CTR-API-BASE
title: "Inherited Orders REST Contract"
status: active
protocol: rest_http
container: CNT-API
specification_file: "src/Api"
---
= Inherited Orders REST Contract

== Endpoints

|===
| Method | Path | Auth
| `GET` | `/api/v1/orders/{id}` | `[Authorize(Policy = "ApiUser")]`
| `GET` | `/api/v1/orders/health` | `[AllowAnonymous]`
| `POST` | `/api/v1/orders` | `[Authorize(Policy = "ApiUser", Roles = "Admin")]`
| `GET` | `/api/v1/status/ping` | `[AllowAnonymous]`
|===
""",
    )

    validator = SpecValidator()
    rep = validator.validate_directory(eidos_dir, check_import_boundaries=False)
    assert rep.is_clean, f"Expected clean inherited contract: {rep.schema_errors} / {rep.broken_references}"
    overlap_warns = [w for w in rep.warnings if "Overlapping source_path" in w]
    assert len(overlap_warns) == 1
    assert "CMP-ALPHA" in overlap_warns[0] and "CMP-BETA" in overlap_warns[0]
    assert "(+1 more)" in overlap_warns[0]

    # Contradiction on inherited base auth must be caught
    _write(
        contract_path,
        """---
type: tech/contract
id: CTR-API-BASE
title: "Inherited Orders REST Contract"
status: active
protocol: rest_http
container: CNT-API
specification_file: "src/Api"
---
= Inherited Orders REST Contract

== Endpoints

|===
| Method | Path | Auth
| `GET` | `/api/v1/orders/{id}` | `[AllowAnonymous]`
| `GET` | `/api/v1/orders/health` | `[AllowAnonymous]`
| `POST` | `/api/v1/orders` | `[Authorize(Policy = "WrongPolicy")]`
| `GET` | `/api/v1/status/ping` | `[AllowAnonymous]`
|===
""",
    )
    bad_rep = validator.validate_directory(eidos_dir, check_import_boundaries=False)
    assert not bad_rep.is_clean
    joined_err = "\n".join(bad_rep.broken_references)
    assert "/api/v1/orders/{*}" in joined_err
    assert "WrongPolicy" in joined_err
