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


def test_asyncapi_and_json_rpc_stdio_bidirectional_parity(tmp_path: Path) -> None:
    """Verify INV-CONTRACT-ASYNCAPI-AND-JSONRPC-PARITY: bidirectional AsyncAPI 2.x/3.x and JSON-RPC/MCP stdio parity."""
    ws = tmp_path / "async_rpc_ws"
    eidos_dir = ws / "eidos"
    (eidos_dir / "architecture" / "containers").mkdir(parents=True)
    (eidos_dir / "architecture" / "contracts").mkdir(parents=True)

    _write(
        ws / "src" / "events" / "asyncapi2.yaml",
        """
asyncapi: 2.6.0
channels:
  orders.created:
    publish:
      operationId: publishOrderCreated
  orders.cancelled:
    subscribe:
      operationId: onOrderCancelled
""",
    )
    _write(
        ws / "src" / "events" / "asyncapi3.yaml",
        """
asyncapi: 3.0.0
channels:
  invoiceChannel:
    address: billing.invoices.paid
operations:
  sendInvoicePaid:
    action: send
""",
    )
    _write(ws / "src" / "events" / "bad_async.yaml", ":\n  - [unclosed")
    _write(ws / "src" / "events" / "list_async.yaml", "- not-a-dict\n")

    _write(
        ws / "src" / "rpc" / "openrpc.json",
        """
{
  "openrpc": "1.3.2",
  "methods": [
    {"name": "ledger.record"},
    {"name": "ledger.balance"}
  ],
  "tools": [
    {"name": "search_docs"}
  ]
}
""",
    )
    _write(
        ws / "src" / "rpc" / "mcp_server.py",
        """
@mcp.tool("list_specs")
async def list_specs_handler():
    return []

@server.tool()
def get_context():
    return {}

def dispatch(method):
    if method == "initialize":
        return {}
""",
    )
    _write(
        ws / "src" / "rpc" / "mcp_server.ts",
        """
server.tool("ts_lookup", async () => ({}));
server.registerMethod("ts.ping", () => "pong");
switch (req.method) {
  case "shutdown":
    break;
}
""",
    )
    _write(ws / "src" / "rpc" / "custom_empty.py", "def handle(raw):\n    return raw\n")
    _write(ws / "src" / "rpc" / "bad_rpc.yaml", "- not-a-mapping\n")

    _write(
        eidos_dir / "architecture" / "containers" / "bus.adoc",
        "---\ntype: tech/container\nid: CNT-BUS\ntitle: Event Bus\nstatus: active\n"
        "container_type: service\nruntime: Python\nsource_path:\n  - src/events\n  - src/rpc\n---\n= Event Bus\n",
    )

    # Clean AsyncAPI 2.x + 3.x + OpenRPC + Python MCP + TS MCP + zero-method fallback
    _write(
        eidos_dir / "architecture" / "contracts" / "a2.adoc",
        "---\ntype: tech/contract\nid: CTR-ASYNC-V2\ntitle: Async V2\nstatus: active\n"
        "protocol: asyncapi\ncontainer: CNT-BUS\nspecification_file: src/events/asyncapi2.yaml\n---\n"
        "= Async V2\n\n* `publish orders.created` (`operation publishOrderCreated`)\n"
        "* `subscribe orders.cancelled` (`operation onOrderCancelled`)\n"
        "Unlike `channel legacy.ignored`, only active channels apply.\n",
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "a3.adoc",
        "---\ntype: tech/contract\nid: CTR-ASYNC-V3\ntitle: Async V3\nstatus: active\n"
        "protocol: asyncapi\ncontainer: CNT-BUS\nspecification_file: src/events/asyncapi3.yaml\n---\n"
        "= Async V3\n\n* `channel billing.invoices.paid` (`operation sendInvoicePaid`)\n",
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "rpc-manifest.adoc",
        "---\ntype: tech/contract\nid: CTR-RPC-MANIFEST\ntitle: OpenRPC\nstatus: active\n"
        "protocol: json_rpc_stdio\ncontainer: CNT-BUS\nspecification_file: src/rpc/openrpc.json\n---\n"
        "= OpenRPC\n\n|===\n| Method | Summary\n| `ledger.record` | Record\n| `ledger.balance` | Balance\n| `search_docs` | Search\n|===\n",
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "rpc-py.adoc",
        "---\ntype: tech/contract\nid: CTR-RPC-PY\ntitle: Python MCP\nstatus: active\n"
        "protocol: json_rpc_stdio\ncontainer: CNT-BUS\nspecification_file: src/rpc/mcp_server.py\n---\n"
        "= Python MCP\n\n* `tool list_specs`\n* `tool get_context`\n* `method initialize`\n"
        "Unlike `method legacy.ignored`, these are active.\n",
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "rpc-ts.adoc",
        "---\ntype: tech/contract\nid: CTR-RPC-TS\ntitle: TS MCP\nstatus: active\n"
        "protocol: json_rpc_stdio\ncontainer: CNT-BUS\nspecification_file: src/rpc/mcp_server.ts\n---\n"
        "= TS MCP\n\n* `tool ts_lookup`\n* `method ts.ping`\n* `method shutdown`\n",
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "rpc-empty.adoc",
        "---\ntype: tech/contract\nid: CTR-RPC-EMPTY\ntitle: Custom Dispatcher\nstatus: active\n"
        "protocol: json_rpc_stdio\ncontainer: CNT-BUS\nspecification_file: src/rpc/custom_empty.py\n---\n"
        "= Custom Dispatcher\n\n* `method dynamic.any`\n",
    )

    validator = SpecValidator()
    clean_rep = validator.validate_directory(eidos_dir, check_import_boundaries=False)
    assert clean_rep.is_clean, (
        f"Expected clean AsyncAPI/JSON-RPC contracts: {clean_rep.schema_errors} / {clean_rep.broken_references}"
    )

    # Now introduce forward + reverse drift and invalid YAML/non-dict files
    _write(
        eidos_dir / "architecture" / "contracts" / "a2.adoc",
        "---\ntype: tech/contract\nid: CTR-ASYNC-V2\ntitle: Async V2\nstatus: active\n"
        "protocol: asyncapi\ncontainer: CNT-BUS\nspecification_file: src/events/asyncapi2.yaml\n---\n"
        "= Async V2\n\n* `channel orders.ghost`\n* `operation ghostOp`\n",
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "rpc-manifest.adoc",
        "---\ntype: tech/contract\nid: CTR-RPC-MANIFEST\ntitle: OpenRPC\nstatus: active\n"
        "protocol: json_rpc_stdio\ncontainer: CNT-BUS\nspecification_file: src/rpc/openrpc.json\n---\n"
        "= OpenRPC\n\n|===\n| Method | Summary\n| `ledger.record` | Record\n| `ledger.ghost` | Phantom\n|===\n"
        "Also `tool phantom_tool`.\n",
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "a-bad.adoc",
        "---\ntype: tech/contract\nid: CTR-ASYNC-BAD\ntitle: Bad Async\nstatus: active\n"
        "protocol: asyncapi\ncontainer: CNT-BUS\nspecification_file: src/events/bad_async.yaml\n---\n= Bad\n",
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "a-list.adoc",
        "---\ntype: tech/contract\nid: CTR-ASYNC-LIST\ntitle: List Async\nstatus: active\n"
        "protocol: asyncapi\ncontainer: CNT-BUS\nspecification_file: src/events/list_async.yaml\n---\n= List\n",
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "rpc-bad.adoc",
        "---\ntype: tech/contract\nid: CTR-RPC-BAD\ntitle: Bad RPC\nstatus: active\n"
        "protocol: json_rpc_stdio\ncontainer: CNT-BUS\nspecification_file: src/rpc/bad_rpc.yaml\n---\n= Bad\n",
    )

    drift_rep = validator.validate_directory(eidos_dir, check_import_boundaries=False)
    assert not drift_rep.is_clean
    joined = "\n".join(drift_rep.broken_references)
    assert "orders.created" in joined
    assert "publishOrderCreated" in joined
    assert "orders.ghost" in joined
    assert "ghostOp" in joined
    assert "ledger.balance" in joined
    assert "ledger.ghost" in joined
    assert "phantom_tool" in joined
    assert "failed to parse" in joined
    assert "root must be a mapping" in joined


def test_polyglot_rest_http_authorization_parity_spring_nestjs_fastapi(tmp_path: Path) -> None:
    """Verify INV-CONTRACT-POLYGLOT-AUTH-PARITY: opt-in Spring, NestJS, and FastAPI authorization parity checks."""
    ws = tmp_path / "polyglot_auth_ws"
    eidos_dir = ws / "eidos"
    (eidos_dir / "architecture" / "containers").mkdir(parents=True)
    (eidos_dir / "architecture" / "contracts").mkdir(parents=True)

    # 1. Java Spring controller with class-level @PreAuthorize, method @Secured/@RolesAllowed, and @PermitAll override
    _write(
        ws / "src" / "spring" / "BillingController.java",
        """
@RestController
@RequestMapping("/api/billing")
@PreAuthorize("hasAuthority('billing:read')")
public class BillingController {
    @GetMapping("/invoices")
    public List<String> listInvoices() { return List.of(); }

    @PostMapping("/refunds")
    @Secured({"ROLE_ADMIN", "ROLE_FINANCE"})
    public void refund() {}

    @DeleteMapping("/invoices/{id}")
    @RolesAllowed("SUPERADMIN")
    public void deleteInvoice() {}

    @GetMapping("/health")
    @PermitAll
    public String health() { return "ok"; }
}

@RestController
@RequestMapping("/api/public-spring")
@PermitAll
public class PublicSpringController {
    @GetMapping("/info")
    public String info() { return "ok"; }

    @PostMapping("/ops")
    @PreAuthorize("hasRole('ROLE_OPS')")
    public void ops() {}
}
""",
    )

    # 2. TypeScript NestJS controller with @UseGuards, @Roles, and @Public() override
    _write(
        ws / "src" / "nestjs" / "orders.controller.ts",
        """
@Controller('api/orders')
@UseGuards(JwtAuthGuard)
export class OrdersController {
  @Get()
  findAll() { return []; }

  @Post()
  @UseGuards(RolesGuard)
  @Roles('admin')
  create() { return {}; }

  @Get('public-status')
  @Public()
  status() { return 'ok'; }
}

@Controller('api/public-nest')
@Public()
export class PublicNestController {
  @Get('info')
  info() { return 'ok'; }
}
""",
    )

    # 3. Python FastAPI router with router-level Depends, include_router dependencies, handler Security/Depends, and open route
    _write(
        ws / "src" / "fastapi_app" / "routes.py",
        """
from fastapi import APIRouter, Depends, FastAPI, Security

app = FastAPI()
admin_router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])
ops_router = APIRouter(prefix="/ops")

@admin_router.get("/metrics")
async def get_metrics():
    return {}

@ops_router.get("/audit")
async def get_audit():
    return {}

app.include_router(ops_router, prefix="/api", dependencies=[Depends(require_ops)])

@app.post("/api/tokens")
async def create_token(ctx = Security(verify_oauth, scopes=["tokens:write"])):
    return {}

@app.get("/api/profile")
async def get_profile(user = Depends(get_current_user)):
    return {}

@app.get("/api/ping")
async def ping():
    return {"ok": True}
""",
    )

    _write(
        eidos_dir / "architecture" / "containers" / "poly.adoc",
        "---\ntype: tech/container\nid: CNT-POLY\ntitle: Polyglot APIs\nstatus: active\n"
        "container_type: service\nruntime: Polyglot\nsource_path:\n  - src/spring\n  - src/nestjs\n  - src/fastapi_app\n---\n= Polyglot APIs\n",
    )

    spring_ctr = eidos_dir / "architecture" / "contracts" / "spring.adoc"
    nest_ctr = eidos_dir / "architecture" / "contracts" / "nest.adoc"
    fa_ctr = eidos_dir / "architecture" / "contracts" / "fastapi.adoc"

    _write(
        spring_ctr,
        """---
type: tech/contract
id: CTR-SPRING
title: Spring Billing API
status: active
protocol: rest_http
container: CNT-POLY
specification_file: src/spring
---
= Spring Billing API

|===
| Method | Path | Auth
| `GET` | `/api/billing/invoices` | `@PreAuthorize("hasAuthority('billing:read')")`
| `POST` | `/api/billing/refunds` | `@Secured({"ROLE_ADMIN"})`
| `DELETE` | `/api/billing/invoices/{id}` | `@RolesAllowed("SUPERADMIN")`
| `GET` | `/api/billing/health` | `@PermitAll`
| `GET` | `/api/public-spring/info` | `@PermitAll`
| `POST` | `/api/public-spring/ops` | `@PreAuthorize("hasRole('OPS')")`
|===
""",
    )
    _write(
        nest_ctr,
        """---
type: tech/contract
id: CTR-NEST
title: NestJS Orders API
status: active
protocol: rest_http
container: CNT-POLY
specification_file: src/nestjs
---
= NestJS Orders API

|===
| Method | Path | Auth
| `GET` | `/api/orders` | `@UseGuards(JwtAuthGuard)`
| `POST` | `/api/orders` | `@UseGuards(JwtAuthGuard, RolesGuard)` `@Roles('admin')`
| `GET` | `/api/orders/public-status` | `@Public()`
| `GET` | `/api/public-nest/info` | `@Public()`
|===
""",
    )
    _write(
        fa_ctr,
        """---
type: tech/contract
id: CTR-FASTAPI
title: FastAPI Service API
status: active
protocol: rest_http
container: CNT-POLY
specification_file: src/fastapi_app
---
= FastAPI Service API

|===
| Method | Path | Auth
| `GET` | `/api/admin/metrics` | `Depends(require_admin)`
| `GET` | `/api/ops/audit` | `Depends(require_ops)`
| `POST` | `/api/tokens` | `Security(verify_oauth, scopes=["tokens:write"])`
| `GET` | `/api/profile` | `Depends(get_current_user)`
| `GET` | `/api/ping` | Public
|===
""",
    )

    validator = SpecValidator()
    clean_rep = validator.validate_directory(eidos_dir, check_import_boundaries=False)
    assert clean_rep.is_clean, (
        f"Expected clean polyglot auth parity: {clean_rep.schema_errors} / {clean_rep.broken_references}"
    )

    # Introduce contradictions across all 3 frameworks
    _write(
        spring_ctr,
        """---
type: tech/contract
id: CTR-SPRING
title: Spring Billing API
status: active
protocol: rest_http
container: CNT-POLY
specification_file: src/spring
---
= Spring Billing API

|===
| Method | Path | Auth
| `GET` | `/api/billing/invoices` | `@PermitAll`
| `POST` | `/api/billing/refunds` | `@RolesAllowed("WRONG_ROLE")`
| `DELETE` | `/api/billing/invoices/{id}` | `@RolesAllowed("SUPERADMIN")`
| `GET` | `/api/billing/health` | `@PreAuthorize("isAuthenticated()")`
|===
""",
    )
    _write(
        nest_ctr,
        """---
type: tech/contract
id: CTR-NEST
title: NestJS Orders API
status: active
protocol: rest_http
container: CNT-POLY
specification_file: src/nestjs
---
= NestJS Orders API

|===
| Method | Path | Auth
| `GET` | `/api/orders` | `@Public()`
| `POST` | `/api/orders` | `@UseGuards(MissingGuard)`
| `GET` | `/api/orders/public-status` | `@UseGuards(JwtAuthGuard)`
|===
""",
    )
    _write(
        fa_ctr,
        """---
type: tech/contract
id: CTR-FASTAPI
title: FastAPI Service API
status: active
protocol: rest_http
container: CNT-POLY
specification_file: src/fastapi_app
---
= FastAPI Service API

|===
| Method | Path | Auth
| `GET` | `/api/admin/metrics` | Public
| `POST` | `/api/tokens` | `Security(verify_oauth, scopes=["wrong:scope"])`
| `GET` | `/api/profile` | `Depends(get_current_user)`
| `GET` | `/api/ping` | `Depends(require_admin)`
|===
""",
    )
    bad_rep = validator.validate_directory(eidos_dir, check_import_boundaries=False)
    assert not bad_rep.is_clean
    joined = "\n".join(bad_rep.broken_references)
    assert "/api/billing/invoices" in joined
    assert "WRONG_ROLE" in joined
    assert "/api/billing/health" in joined
    assert "/api/orders" in joined
    assert "MissingGuard" in joined
    assert "/api/orders/public-status" in joined
    assert "/api/admin/metrics" in joined
    assert "wrong:scope" in joined
    assert "/api/ping" in joined


def test_extended_contract_protocols_bidirectional_parity(tmp_path: Path) -> None:
    """Verify INV-CONTRACT-EXTENDED-PROTOCOLS-PARITY: static bidirectional parity, malformed-schema diagnostics,
    zero-symbol fallbacks, multi-file globs, SignalR opt-in auth checks, and curated portal labels across
    code_interface, wsdl, avro, sql_ddl, trpc, websocket, and mcp."""
    from eidos.portal.mermaid import _humanize_protocol

    # Curated portal display labels for all 7 new protocols
    expected_labels = {
        "code_interface": "Code Interface",
        "wsdl": "WSDL",
        "avro": "Avro",
        "sql_ddl": "SQL DDL",
        "trpc": "tRPC",
        "websocket": "WebSocket",
        "mcp": "MCP",
    }
    for proto, label in expected_labels.items():
        assert _humanize_protocol(proto) == label

    repo = tmp_path / "ext_proto_repo"
    eidos_dir = repo / "eidos"
    (eidos_dir / "architecture" / "containers").mkdir(parents=True)
    (eidos_dir / "architecture" / "contracts").mkdir(parents=True)
    _write(
        eidos_dir / "architecture" / "containers" / "platform.adoc",
        "---\ntype: tech/container\nid: CNT-PLAT\ntitle: Platform\nstatus: active\ncontainer_type: service\nruntime: Polyglot\nsource_path: src\n---\n= Platform\n",
    )

    # 1. WSDL 1.1 & 2.0 + malformed XML + unsafe DTD rejection
    _write(
        repo / "contracts" / "billing.wsdl",
        '<?xml version="1.0"?>\n'
        '<definitions xmlns="http://schemas.xmlsoap.org/wsdl/" name="Billing">\n'
        '  <portType name="BillingPort"><operation name="ChargeCard"/><operation name="RefundCharge"/></portType>\n'
        '  <service name="BillingService"/>\n'
        "</definitions>\n",
    )
    _write(
        repo / "contracts" / "shipping_v2.wsdl",
        '<?xml version="1.0"?>\n'
        '<description xmlns="http://www.w3.org/ns/wsdl">\n'
        '  <interface name="ShipIface"><operation name="DispatchParcel"/></interface>\n'
        '  <service name="ShippingService"/>\n'
        "</description>\n",
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "wsdl_ok.adoc",
        "---\ntype: tech/contract\nid: CTR-WSDL-OK\ntitle: Billing WSDL\nstatus: active\nprotocol: wsdl\ncontainer: CNT-PLAT\nspecification_file: contracts/billing.wsdl\n---\n"
        "= Billing WSDL\n\n== Operations\n|===\n| Operation | Notes\n| `ChargeCard` | Charges card\n| `RefundCharge` | Refunds\n|===\n"
        "Exposed by `service BillingService`. Unlike `operation LegacyCancel`, removed ops are excluded.\n",
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "wsdl_v2.adoc",
        "---\ntype: tech/contract\nid: CTR-WSDL-V2\ntitle: Shipping WSDL\nstatus: active\nprotocol: wsdl\ncontainer: CNT-PLAT\nspecification_file: contracts/shipping_v2.wsdl\n---\n"
        "= Shipping WSDL\nExposes `service ShippingService` and `operation DispatchParcel`.\n",
    )

    # 2. Avro .avsc + .avdl
    _write(
        repo / "schemas" / "order.avsc",
        '{"type":"record","name":"OrderPlaced","fields":[{"name":"orderId","type":"string"},{"name":"status","type":{"type":"enum","name":"OrderState","symbols":["NEW","PAID"]}}]}\n',
    )
    _write(
        repo / "schemas" / "ledger.avdl",
        "protocol LedgerProtocol {\n  record Entry { string id; }\n  void postEntry(Entry e);\n}\n",
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "avro_json.adoc",
        "---\ntype: tech/contract\nid: CTR-AVRO-JSON\ntitle: Order Avro\nstatus: active\nprotocol: avro\ncontainer: CNT-PLAT\nspecification_file: schemas/order.avsc\n---\n"
        "= Order Avro\nRecord `record OrderPlaced` has fields `orderId` and `status` of enum `enum OrderState`.\n",
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "avro_idl.adoc",
        "---\ntype: tech/contract\nid: CTR-AVRO-IDL\ntitle: Ledger Avro IDL\nstatus: active\nprotocol: avro\ncontainer: CNT-PLAT\nspecification_file: schemas/ledger.avdl\n---\n"
        "= Ledger Avro IDL\nProtocol `LedgerProtocol` defines `record Entry` and `message postEntry`.\n",
    )

    # 3. SQL DDL (.sql + .prisma across directory)
    _write(
        repo / "db" / "schema.sql",
        "-- core schema\nCREATE TABLE IF NOT EXISTS public.accounts (id INT);\nCREATE OR REPLACE VIEW active_accounts AS SELECT * FROM accounts;\n",
    )
    _write(
        repo / "db" / "schema.prisma",
        "model Invoice {\n  id Int @id\n}\nenum InvoiceStatus {\n  OPEN\n}\n",
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "sql_ok.adoc",
        "---\ntype: tech/contract\nid: CTR-SQL-OK\ntitle: Core DB Schema\nstatus: active\nprotocol: sql_ddl\ncontainer: CNT-PLAT\nspecification_file: db\n---\n"
        "= Core DB Schema\n\n== Database Objects\n|===\n| Object | Kind\n| `accounts` | Table\n| `active_accounts` | View\n| `Invoice` | Model\n| `InvoiceStatus` | Enum\n|===\n",
    )

    # 4. Polyglot code_interface (TS, C#, Go, Rust, Java)
    _write(repo / "src" / "sdk" / "client.d.ts", "export interface SdkClient {\n  fetchToken(id: string): Promise<string>;\n}\n")
    _write(repo / "src" / "sdk" / "IStore.cs", "public interface IStore {\n  void SaveItem(string key);\n}\n")
    _write(repo / "src" / "sdk" / "cache.go", "package sdk\ntype CacheStore interface {\n  GetEntry(k string) string\n}\n")
    _write(repo / "src" / "sdk" / "codec.rs", "pub trait Codec {\n  fn encode_frame(&self) -> Vec<u8>;\n}\n")
    _write(repo / "src" / "sdk" / "Mailer.java", "public interface Mailer {\n  void sendReceipt(String to);\n}\n")
    _write(
        eidos_dir / "architecture" / "contracts" / "sdk_ok.adoc",
        "---\ntype: tech/contract\nid: CTR-SDK-OK\ntitle: Polyglot SDK\nstatus: active\nprotocol: code_interface\ncontainer: CNT-PLAT\nspecification_file: src/sdk\n---\n"
        "= Polyglot SDK\n\n== Interfaces\n"
        "* `interface SdkClient`: `method fetchToken`\n"
        "* `interface IStore`: `method SaveItem`\n"
        "* `interface CacheStore`: `method GetEntry`\n"
        "* `trait Codec`: `method encode_frame`\n"
        "* `interface Mailer`: `method sendReceipt`\n",
    )

    # 5. tRPC router
    _write(
        repo / "src" / "trpc" / "router.ts",
        "export const appRouter = createTRPCRouter({\n"
        "  getUser: publicProcedure.query(() => null),\n"
        "  updateUser: protectedProcedure.mutation(() => true),\n"
        "  onAlert: publicProcedure.subscription(() => null),\n"
        "});\n",
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "trpc_ok.adoc",
        "---\ntype: tech/contract\nid: CTR-TRPC-OK\ntitle: App tRPC\nstatus: active\nprotocol: trpc\ncontainer: CNT-PLAT\nspecification_file: src/trpc/router.ts\n---\n"
        "= App tRPC\n\n== Procedures\n|===\n| Procedure | Type\n| `getUser` | query\n| `updateUser` | mutation\n| `onAlert` | subscription\n|===\n",
    )

    # 6. WebSocket: ASP.NET Core SignalR Hub (with [Authorize]/[AllowAnonymous]) + NestJS + Socket.IO
    _write(
        repo / "src" / "ws" / "ChatHub.cs",
        '[Authorize(Policy = "ChatUser", Roles = "Member")]\n'
        "public class ChatHub : Hub {\n"
        "  public async Task SendMessage(string msg) {}\n"
        "  [AllowAnonymous]\n"
        "  public Task PingStatus() { return Task.CompletedTask; }\n"
        "}\n",
    )
    _write(
        repo / "src" / "ws" / "gateway.ts",
        '@SubscribeMessage("join_room")\nhandleJoin() {}\nsocket.on("typing_start", () => {});\nsocket.on("disconnect", () => {});\n',
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "ws_ok.adoc",
        "---\ntype: tech/contract\nid: CTR-WS-OK\ntitle: Realtime Events\nstatus: active\nprotocol: websocket\ncontainer: CNT-PLAT\nspecification_file: src/ws\n---\n"
        "= Realtime Events\n\n== Hub & Events\n"
        '|===\n| Method | Auth\n| `SendMessage` | `[Authorize(Policy = "ChatUser", Roles = "Member")]`\n| `PingStatus` | `[AllowAnonymous]`\n| `join_room` | Open\n| `typing_start` | Open\n|===\n',
    )

    # 7. Transport-agnostic MCP (YAML manifest + Python/TS server sources)
    _write(
        repo / "src" / "mcp" / "manifest.yaml",
        "tools:\n  - name: search_docs\nresources:\n  - uri: eidos://catalog\nprompts:\n  - name: review_delta\n",
    )
    _write(
        repo / "src" / "mcp" / "server.py",
        '@mcp.tool()\ndef ping_health(): pass\n@mcp.resource("eidos://status")\ndef get_status(): pass\n@mcp.prompt("summarize")\ndef sum_p(): pass\n',
    )
    _write(
        repo / "src" / "mcp" / "server.ts",
        'server.registerTool("ts_lookup", {}, async () => ({}));\nserver.registerResource("eidos://ts", async () => ({}));\nserver.registerPrompt("ts_guide", async () => ({}));\n',
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "mcp_ok.adoc",
        "---\ntype: tech/contract\nid: CTR-MCP-OK\ntitle: Agent MCP\nstatus: active\nprotocol: mcp\ncontainer: CNT-PLAT\nspecification_file: src/mcp\n---\n"
        "= Agent MCP\n\n== Capabilities\n"
        "* `tool search_docs`, `resource eidos://catalog`, `prompt review_delta`\n"
        "* `tool ping_health`, `resource eidos://status`, `prompt summarize`\n"
        "* `tool ts_lookup`, `resource eidos://ts`, `prompt ts_guide`\n",
    )

    # Zero-symbol fallback: empty source file must not false-positive
    _write(repo / "src" / "empty" / "custom.ts", "// custom wrapper with no tRPC router\nexport const x = 1;\n")
    _write(
        eidos_dir / "architecture" / "contracts" / "trpc_empty.adoc",
        "---\ntype: tech/contract\nid: CTR-TRPC-EMPTY\ntitle: Custom Wrapper\nstatus: active\nprotocol: trpc\ncontainer: CNT-PLAT\nspecification_file: src/empty/custom.ts\n---\n"
        "= Custom Wrapper\nDocuments `query phantomIgnored` safely under zero-symbol fallback.\n",
    )

    validator = SpecValidator()
    clean_rep = validator.validate_directory(eidos_dir, check_import_boundaries=False)
    assert clean_rep.is_clean, f"Expected clean extended protocols validation, got: {clean_rep.broken_references}"

    # Now exercise forward drift, reverse phantom drift, SignalR auth contradiction, malformed XML/JSON/MCP, and unsafe DTD
    _write(repo / "contracts" / "bad.wsdl", "<definitions><unclosed></definitions>")
    _write(repo / "contracts" / "dtd.wsdl", '<!DOCTYPE foo [<!ENTITY xxe "bad">]><definitions/>')
    _write(repo / "schemas" / "bad.avsc", "{not-json")
    _write(repo / "src" / "mcp_bad" / "bad.json", "{bad: [yaml")

    _write(
        eidos_dir / "architecture" / "contracts" / "wsdl_bad.adoc",
        "---\ntype: tech/contract\nid: CTR-WSDL-BAD\ntitle: Bad WSDL\nstatus: active\nprotocol: wsdl\ncontainer: CNT-PLAT\nspecification_file: contracts/bad.wsdl\n---\n= Bad\n",
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "wsdl_dtd.adoc",
        "---\ntype: tech/contract\nid: CTR-WSDL-DTD\ntitle: DTD WSDL\nstatus: active\nprotocol: wsdl\ncontainer: CNT-PLAT\nspecification_file: contracts/dtd.wsdl\n---\n= DTD\n",
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "avro_bad.adoc",
        "---\ntype: tech/contract\nid: CTR-AVRO-BAD\ntitle: Bad Avro\nstatus: active\nprotocol: avro\ncontainer: CNT-PLAT\nspecification_file: schemas/bad.avsc\n---\n= Bad\n",
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "mcp_bad.adoc",
        "---\ntype: tech/contract\nid: CTR-MCP-BAD\ntitle: Bad MCP\nstatus: active\nprotocol: mcp\ncontainer: CNT-PLAT\nspecification_file: src/mcp_bad/bad.json\n---\n= Bad\n",
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "drift_mix.adoc",
        "---\ntype: tech/contract\nid: CTR-DRIFT-WS\ntitle: Drift WS\nstatus: active\nprotocol: websocket\ncontainer: CNT-PLAT\nspecification_file: src/ws\n---\n"
        "= Drift WS\n\n== Hub & Events\n"
        "|===\n| Method | Auth\n"
        '| `SendMessage` | `[AllowAnonymous]`\n'
        '| `PingStatus` | `[Authorize(Policy = "WrongPol")]`\n'
        "| `GhostEvent` | Open\n|===\n"
        'Also `SendMessage` with `[Authorize(Policy = "WrongPol", Roles = "WrongRole")]`.\n',
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "drift_trpc.adoc",
        "---\ntype: tech/contract\nid: CTR-DRIFT-TRPC\ntitle: Drift tRPC\nstatus: active\nprotocol: trpc\ncontainer: CNT-PLAT\nspecification_file: src/trpc/router.ts\n---\n"
        "= Drift tRPC\nExposes `query getUser` and `mutation deletedProc`.\n",
    )

    bad_rep = validator.validate_directory(eidos_dir, check_import_boundaries=False)
    assert not bad_rep.is_clean
    joined = "\n".join(bad_rep.broken_references)
    assert "failed to parse" in joined
    assert "disallowed XML DTD/ENTITY" in joined
    assert "GhostEvent" in joined
    assert "join_room" in joined
    assert "WrongPol" in joined
    assert "WrongRole" in joined
    assert "deletedProc" in joined
    assert "updateUser" in joined


def test_polyglot_cli_openapi_webhooks_and_json_schema_parity(tmp_path: Path) -> None:
    """Verify INV-CONTRACT-CLI-OPENAPI-JSONSCHEMA-PARITY: polyglot cli_args across Go cobra, Rust clap,
    Node/TS commander/yargs, and C# System.CommandLine (plus safe Python directory delegation & zero-command fallback),
    bidirectional OpenAPI 3.1 webhooks parity alongside paths and webhook-only specs, and bidirectional json_schema
    $defs/definitions and recursive bare-identifier reverse phantom parity."""
    repo = tmp_path / "cli_oas_js_repo"
    eidos_dir = repo / "eidos"
    (eidos_dir / "architecture" / "containers").mkdir(parents=True)
    (eidos_dir / "architecture" / "contracts").mkdir(parents=True)
    _write(
        eidos_dir / "architecture" / "containers" / "tools.adoc",
        "---\ntype: tech/container\nid: CNT-TOOLS\ntitle: Tools\nstatus: active\ncontainer_type: cli\nruntime: Polyglot\nsource_path: src\n---\n= Tools\n",
    )

    # 1. Polyglot cli_args: Go cobra, Rust clap, Node commander, C# System.CommandLine, Python dir, and zero-cmd fallback
    _write(
        repo / "src" / "cli_go" / "cmd.go",
        'package main\nvar deployCmd = &cobra.Command{Use: "deploy [env]"}\nfunc init() { deployCmd.Flags().String("region", "us", "r") }\n',
    )
    _write(
        repo / "src" / "cli_rs" / "main.rs",
        "#[derive(Subcommand)]\nenum Commands {\n  SyncState,\n  #[allow(dead_code)]\n  CheckDrift,\n}\n"
        'fn extra() { Command::new("status"); }\n'
        "#[arg(long)]\npub dry_run: bool,\n",
    )
    _write(
        repo / "src" / "cli_ts" / "index.ts",
        'program.command("publish <pkg>").option("-f, --force", "force");\n',
    )
    _write(
        repo / "src" / "cli_cs" / "Program.cs",
        'var cmd = new Command("migrate");\nvar opt = new Option<bool>("--verbose");\n',
    )
    _write(
        repo / "src" / "cli_py" / "main.py",
        "import typer\napp = typer.Typer()\n@app.command('hello')\ndef hello_cmd(): pass\n",
    )
    _write(repo / "src" / "cli_empty" / "main.go", "package main\nfunc main() {}\n")

    _write(
        eidos_dir / "architecture" / "contracts" / "cli_go.adoc",
        "---\ntype: tech/contract\nid: CTR-CLI-GO\ntitle: Go CLI\nstatus: active\nprotocol: cli_args\ncontainer: CNT-TOOLS\nspecification_file: src/cli_go\n---\n"
        "= Go CLI\n\n== Commands\n|===\n| Command | Flags\n| `deploy` | `--region`\n|===\n",
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "cli_rs.adoc",
        "---\ntype: tech/contract\nid: CTR-CLI-RS\ntitle: Rust CLI\nstatus: active\nprotocol: cli_args\ncontainer: CNT-TOOLS\nspecification_file: src/cli_rs/main.rs\n---\n"
        "= Rust CLI\nCommands: `command sync-state`, `command check-drift`, `command status` with `--dry-run`.\n",
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "cli_ts.adoc",
        "---\ntype: tech/contract\nid: CTR-CLI-TS\ntitle: Node CLI\nstatus: active\nprotocol: cli_args\ncontainer: CNT-TOOLS\nspecification_file: src/cli_ts/index.ts\n---\n"
        "= Node CLI\nSupports `command publish` and `--force`.\n",
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "cli_cs.adoc",
        "---\ntype: tech/contract\nid: CTR-CLI-CS\ntitle: Dotnet CLI\nstatus: active\nprotocol: cli_args\ncontainer: CNT-TOOLS\nspecification_file: src/cli_cs/Program.cs\n---\n"
        "= Dotnet CLI\nSupports `command migrate` and `--verbose`.\n",
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "cli_py_dir.adoc",
        "---\ntype: tech/contract\nid: CTR-CLI-PY-DIR\ntitle: Py Dir CLI\nstatus: active\nprotocol: cli_args\ncontainer: CNT-TOOLS\nspecification_file: src/cli_py\n---\n"
        "= Py Dir CLI\n\n== Commands\n|===\n| Command | Description\n| `hello` | Say hello\n|===\n",
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "cli_empty.adoc",
        "---\ntype: tech/contract\nid: CTR-CLI-EMPTY\ntitle: Empty CLI\nstatus: active\nprotocol: cli_args\ncontainer: CNT-TOOLS\nspecification_file: src/cli_empty/main.go\n---\n"
        "= Empty CLI\nCustom `command ignored-cmd` under zero-command fallback.\n",
    )

    # 2. OpenAPI 3.1 webhooks (both alongside paths and webhook-only)
    _write(
        repo / "api" / "mixed.openapi.yaml",
        "openapi: 3.1.0\ninfo: {title: Mixed, version: 1.0.0}\n"
        "paths:\n  /v1/orders:\n    post: {}\n"
        "webhooks:\n  orderCreated:\n    post: {}\n",
    )
    _write(
        repo / "api" / "webhooks_only.openapi.yaml",
        "openapi: 3.1.0\ninfo: {title: Hooks, version: 1.0.0}\n"
        "webhooks:\n  invoicePaid:\n    post: {}\n",
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "oas_mixed.adoc",
        "---\ntype: tech/contract\nid: CTR-OAS-MIXED\ntitle: Mixed OpenAPI\nstatus: active\nprotocol: openapi\ncontainer: CNT-TOOLS\nspecification_file: api/mixed.openapi.yaml\n---\n"
        "= Mixed OpenAPI\n\n== Endpoints & Webhooks\n* `POST /v1/orders`\n* `webhook orderCreated`\n",
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "oas_hooks.adoc",
        "---\ntype: tech/contract\nid: CTR-OAS-HOOKS\ntitle: Webhook Only OpenAPI\nstatus: active\nprotocol: openapi\ncontainer: CNT-TOOLS\nspecification_file: api/webhooks_only.openapi.yaml\n---\n"
        "= Webhook Only OpenAPI\n\n== Webhooks\n|===\n| Webhook | Event\n| `invoicePaid` | Paid\n|===\n",
    )

    # 3. Bidirectional json_schema ($defs-only library + root properties with nested/optional fields + dotted paths)
    _write(
        repo / "schemas" / "defs_only.json",
        '{"$schema":"https://json-schema.org/draft/2020-12/schema","$defs":{"Money":{"type":"object","properties":{"amount":{"type":"number"}}},"Currency":{"type":"string"}}}\n',
    )
    _write(
        repo / "schemas" / "order_payload.json",
        '{"type":"object","required":["id"],"properties":{"id":{"type":"string"},"note":{"type":"string"},"customer":{"type":"object","properties":{"email":{"type":"string"}}},"items":{"type":"array","items":{"type":"object","properties":{"sku":{"type":"string"}}}}}}\n',
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "js_defs.adoc",
        "---\ntype: tech/contract\nid: CTR-JS-DEFS\ntitle: Shared Defs\nstatus: active\nprotocol: json_schema\ncontainer: CNT-TOOLS\nspecification_file: schemas/defs_only.json\n---\n"
        "= Shared Defs\n\n== Definitions\n|===\n| Definition | Description\n| `Money` | Monetary amount\n| `Currency` | ISO currency\n| `amount` | Nested numeric field\n|===\n",
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "js_order.adoc",
        "---\ntype: tech/contract\nid: CTR-JS-ORDER\ntitle: Order Payload\nstatus: active\nprotocol: json_schema\ncontainer: CNT-TOOLS\nspecification_file: schemas/order_payload.json\n---\n"
        "= Order Payload\n\n== Properties\n|===\n| Property | Notes\n"
        "| `id` | Required\n| `note` | Optional top-level\n| `email` | Nested customer field\n"
        "| `sku` | Nested item field\n| `customer.email` | Dotted path skipped cleanly\n| `items[].sku` | Bracketed path skipped\n|===\n"
        "Unlike `property legacyRemoved`, old fields are excluded.\n",
    )

    validator = SpecValidator()
    clean_rep = validator.validate_directory(eidos_dir, check_import_boundaries=False)
    assert clean_rep.is_clean, f"Expected clean validation, got: {clean_rep.broken_references}"

    # Inject forward + reverse drift across polyglot CLI, OpenAPI webhooks, and json_schema $defs/phantom properties
    _write(
        eidos_dir / "architecture" / "contracts" / "cli_go.adoc",
        "---\ntype: tech/contract\nid: CTR-CLI-GO\ntitle: Go CLI\nstatus: active\nprotocol: cli_args\ncontainer: CNT-TOOLS\nspecification_file: src/cli_go\n---\n"
        "= Go CLI\n\n== Commands\n|===\n| Command | Flags\n| `ghost-cmd` | `--ghost-flag`\n|===\n",
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "oas_hooks.adoc",
        "---\ntype: tech/contract\nid: CTR-OAS-HOOKS\ntitle: Webhook Only OpenAPI\nstatus: active\nprotocol: openapi\ncontainer: CNT-TOOLS\nspecification_file: api/webhooks_only.openapi.yaml\n---\n"
        "= Webhook Only OpenAPI\n\n== Webhooks\n|===\n| Webhook | Event\n| `deletedHook` | Removed\n|===\n",
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "js_defs.adoc",
        "---\ntype: tech/contract\nid: CTR-JS-DEFS\ntitle: Shared Defs\nstatus: active\nprotocol: json_schema\ncontainer: CNT-TOOLS\nspecification_file: schemas/defs_only.json\n---\n"
        "= Shared Defs\n\n== Definitions\n|===\n| Definition | Description\n| `Money` | Present\n| `PhantomDef` | Removed\n|===\n",
    )
    _write(
        eidos_dir / "architecture" / "contracts" / "js_order.adoc",
        "---\ntype: tech/contract\nid: CTR-JS-ORDER\ntitle: Order Payload\nstatus: active\nprotocol: json_schema\ncontainer: CNT-TOOLS\nspecification_file: schemas/order_payload.json\n---\n"
        "= Order Payload\n\n== Properties\n|===\n| Property | Notes\n| `id` | Required\n| `removedField` | Phantom\n|===\n",
    )

    bad_rep = validator.validate_directory(eidos_dir, check_import_boundaries=False)
    assert not bad_rep.is_clean
    joined = "\n".join(bad_rep.broken_references)
    assert "deploy" in joined and "ghost-cmd" in joined and "--region" in joined and "--ghost-flag" in joined
    assert "invoicePaid" in joined and "deletedHook" in joined
    assert "Currency" in joined and "PhantomDef" in joined and "removedField" in joined
