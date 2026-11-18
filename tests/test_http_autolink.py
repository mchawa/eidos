"""Tests verifying polyglot static frontend-to-backend HTTP auto-linking (DLT-2026-137)."""

from __future__ import annotations

from pathlib import Path

from eidos.core.index.ast_indexer import AstGrepIndexer, get_ast_indexer, normalize_http_route_path
from eidos.core.index.graph_index import ArchitecturalIndex
from eidos.core.lifecycle.initializer import init_repository
from eidos.core.lifecycle.scaffolder import create_spec
from eidos.core.spec.parser import dump_spec_document, parse_spec_file


def test_static_frontend_request_to_backend_endpoint_autolink(tmp_path: Path) -> None:
    """Verify INV-INDEX-STATIC-HTTP-AUTOLINK: deterministic polyglot HTTP endpoint/request AST extraction and ast_http linking."""
    idx_ast = get_ast_indexer()
    assert isinstance(idx_ast, AstGrepIndexer)

    # 1. Verify dynamic route segment normalization across frameworks
    assert normalize_http_route_path("https://api.example.com/v1/orders/:id?expand=items") == "/v1/orders/{*}"
    assert normalize_http_route_path("/users/<int:user_id>/posts/[postId]/") == "/users/{*}/posts/{*}"
    assert normalize_http_route_path("/docs/[...slug]") == "/docs/{*}"
    assert normalize_http_route_path("/orders/${orderId}/items/{item_id:uuid}") == "/orders/{*}/items/{*}"
    assert normalize_http_route_path("/") == "/"

    # 2. Verify polyglot server endpoint extraction across all 7 language families
    # (a) Python: FastAPI APIRouter + Flask Blueprint + Starlette Route
    py_srv = (
        'from fastapi import APIRouter\n'
        'from flask import Blueprint\n'
        'from starlette.routing import Route\n'
        'router = APIRouter(prefix="/v1")\n'
        '@router.get("/orders/{order_id}")\n'
        'async def get_order(order_id: str):\n'
        '    return {}\n'
        'bp = Blueprint("users", __name__, url_prefix="/api")\n'
        '@bp.route("/users/<int:user_id>", methods=["GET", "DELETE"])\n'
        'def user_handler(user_id: int):\n'
        '    return {}\n'
        'routes = [Route("/healthz", endpoint=user_handler, methods=["GET"])]\n'
    )
    py_eps = {s.name for s in idx_ast.extract_http_endpoints("src/server.py", py_srv)}
    assert {
        "GET /v1/orders/{*}",
        "GET /api/users/{*}",
        "DELETE /api/users/{*}",
        "GET /healthz",
    } <= py_eps

    # (b) TypeScript: Express/Hono + NestJS Controller + Next.js App Router
    ts_express = (
        'const BASE = "/api";\n'
        'app.post(`${BASE}/payments/:paymentId`, handler);\n'
        '@Controller("billing")\n'
        'export class BillingController {\n'
        '  @Get("invoices/:id")\n'
        '  findOne() {}\n'
        '  @Post()\n'
        '  create() {}\n'
        '}\n'
    )
    ts_eps = {s.name for s in idx_ast.extract_http_endpoints("src/billing.controller.ts", ts_express)}
    assert {"POST /api/payments/{*}", "GET /billing/invoices/{*}", "POST /billing"} <= ts_eps

    next_route = (
        'export async function GET(req: Request) { return Response.json({}); }\n'
        'export const PATCH = async (req: Request) => Response.json({});\n'
    )
    next_eps = {
        s.name
        for s in idx_ast.extract_http_endpoints("apps/web/src/app/api/orders/[id]/route.ts", next_route)
    }
    assert next_eps == {"GET /api/orders/{*}", "PATCH /api/orders/{*}"}
    next_catchall = {
        s.name
        for s in idx_ast.extract_http_endpoints("src/app/(marketing)/docs/[...slug]/route.tsx", "export function GET() {}")
    }
    assert next_catchall == {"GET /docs/{*}"}

    # (c) Go: net/http + Gin group
    go_srv = (
        'package main\n'
        'func register(r *gin.Engine) {\n'
        '    http.HandleFunc("GET /go/ping", ping)\n'
        '    v1 := r.Group("/api/v1")\n'
        '    v1.POST("/shipments/:id", ship)\n'
        '}\n'
    )
    go_eps = {s.name for s in idx_ast.extract_http_endpoints("cmd/server/main.go", go_srv)}
    assert {"GET /go/ping", "POST /api/v1/shipments/{*}"} <= go_eps

    # (d) Rust: axum Router::route + actix-web attribute
    rs_srv = (
        '#[get("/actix/orders/{id}")]\n'
        'async fn actix_order() {}\n'
        'fn app() -> Router {\n'
        '    Router::new().route("/rust/items/{id}", get(get_item).post(create_item))\n'
        '}\n'
    )
    rs_eps = {s.name for s in idx_ast.extract_http_endpoints("src/main.rs", rs_srv)}
    assert {"GET /actix/orders/{*}", "GET /rust/items/{*}", "POST /rust/items/{*}"} <= rs_eps

    # (e) Java / Kotlin Spring
    kt_srv = (
        '@RestController\n'
        '@RequestMapping("/spring")\n'
        'class AccountController {\n'
        '    @GetMapping("/accounts/{id}")\n'
        '    fun getAccount() {}\n'
        '    @PostMapping("/accounts")\n'
        '    fun createAccount() {}\n'
        '}\n'
    )
    kt_eps = {s.name for s in idx_ast.extract_http_endpoints("src/AccountController.kt", kt_srv)}
    assert {"GET /spring/accounts/{*}", "POST /spring/accounts"} <= kt_eps

    # (f) C# ASP.NET Core Controller + Minimal API MapGroup
    cs_srv = (
        '[ApiController]\n'
        '[Route("api/[controller]")]\n'
        'public class OrdersController : ControllerBase {\n'
        '    [HttpGet("{id:guid}")]\n'
        '    public IActionResult Get(Guid id) => Ok();\n'
        '    [HttpPost]\n'
        '    public IActionResult Create() => Ok();\n'
        '}\n'
        'var grp = app.MapGroup("/minimal");\n'
        'grp.MapPut("/items/{id}", () => Results.Ok());\n'
    )
    cs_eps = {s.name for s in idx_ast.extract_http_endpoints("Controllers/OrdersController.cs", cs_srv)}
    assert {"GET /api/orders/{*}", "POST /api/orders", "PUT /minimal/items/{*}"} <= cs_eps

    # 3. Verify polyglot client HTTP request extraction (fetch, EventSource, axios, ky, Angular, Vue SFC, Python)
    ts_client = (
        'const API_PREFIX = "/v1";\n'
        'const apiUrl = (p: string) => `${API_PREFIX}${p}`;\n'
        'const api = axios.create({ baseURL: "/billing" });\n'
        'const kyApi = ky.create({ prefixUrl: "/api" });\n'
        'export async function loadAll(orderId: string, invId: string) {\n'
        '  // fetch("http://ignored-comment/fake")\n'
        '  await window.fetch(apiUrl(`/orders/${orderId}`));\n'
        '  await fetch(`/api/payments/${invId}`, { method: "POST" });\n'
        '  const es = new window.EventSource("/api/events");\n'
        '  await api.get(`/invoices/${invId}`);\n'
        '  await api.post("/invoices", { amount: 100 });\n'
        '  await axios({ method: "POST", url: "/billing" });\n'
        '  await kyApi.delete(`users/${orderId}`);\n'
        '  await kyApi.post("users", { json: { id: orderId } });\n'
        '  await this.apiClient.post("/orders", { id: orderId });\n'
        '  this.http.put<void>(`/minimal/items/${orderId}`, {});\n'
        '}\n'
    )
    ts_reqs = {s.name for s in idx_ast.extract_http_requests("src/client/api.ts", ts_client)}
    assert {
        "GET /v1/orders/{*}",
        "POST /api/payments/{*}",
        "GET /api/events",
        "GET /billing/invoices/{*}",
        "POST /billing/invoices",
        "POST /billing",
        "DELETE /api/users/{*}",
        "POST /api/users",
        "PUT /minimal/items/{*}",
    } <= ts_reqs
    # 2-argument frontend axios/ky/this.* client calls MUST NEVER emit phantom server http_endpoint symbols
    assert idx_ast.extract_http_endpoints("src/client/api.ts", ts_client) == []

    vue_sfc = (
        '<template><div>Orders</div></template>\n'
        '<script setup lang="ts">\n'
        'fetch(`/spring/accounts/${accountId}`);\n'
        '</script>\n'
    )
    assert {s.name for s in idx_ast.extract_http_requests("src/OrdersView.vue", vue_sfc)} == {
        "GET /spring/accounts/{*}"
    }

    py_client = (
        'import httpx, requests\n'
        'API_ROOT = "https://internal.local/api/v1"\n'
        'def sync_shipment(sid: str):\n'
        '    requests.get("http://localhost:8000/healthz")\n'
        '    with httpx.Client(base_url=API_ROOT) as client:\n'
        '        client.post(f"/shipments/{sid}")\n'
    )
    py_reqs = {s.name for s in idx_ast.extract_http_requests("src/worker/client.py", py_client)}
    assert {"GET /healthz", "POST /api/v1/shipments/{*}"} <= py_reqs

    # 4. End-to-end workspace sync: CMP-* client, container-direct CNT-* SPA, OpenAPI + http_json CTR-*,
    #    single-candidate prefix disambiguation, multi-candidate ambiguity guard, and is_test=1 exclusion
    repo = tmp_path / "polyglot_http_repo"
    repo.mkdir()
    init_repository(repo, title="Polyglot HTTP AutoLink Repo")
    eidos_dir = repo / "eidos"

    create_spec(eidos_dir, "domain", "DOM-COMMERCE", title="Commerce Domain")
    cnt_web = create_spec(eidos_dir, "container", "CNT-WEB", title="Web Portal")
    cnt_spa = create_spec(eidos_dir, "container", "CNT-SPA", title="Direct SPA Client")
    cnt_api = create_spec(eidos_dir, "container", "CNT-API", title="Backend API")
    cnt_pay = create_spec(eidos_dir, "container", "CNT-PAYMENTS", title="Payments Service")
    cmp_web = create_spec(eidos_dir, "component", "CMP-WEB-API-CLIENT", title="Web API Client")
    cmp_api = create_spec(eidos_dir, "component", "CMP-ORDER-CONTROLLER", title="Order Controller")
    ctr_openapi = create_spec(eidos_dir, "contract", "CTR-PAYMENTS-OPENAPI", title="Payments OpenAPI")
    ctr_events = create_spec(eidos_dir, "contract", "CTR-EVENTS-JSON", title="Events HTTP JSON")

    for p_cnt, spath, provides in (
        (cnt_web, "apps/web", []),
        (cnt_spa, "apps/spa", []),
        (cnt_api, "services/api", ["CTR-EVENTS-JSON"]),
        (cnt_pay, "services/payments", ["CTR-PAYMENTS-OPENAPI"]),
    ):
        d = parse_spec_file(p_cnt)
        d.frontmatter["status"] = "active"
        d.frontmatter["domain"] = "DOM-COMMERCE"
        d.frontmatter["source_path"] = spath
        d.frontmatter["contracts"] = {"provides": provides, "consumes": []}
        p_cnt.write_text(dump_spec_document(d.frontmatter, d.body), encoding="utf-8")

    for p_cmp, parent_cnt, spath in (
        (cmp_web, "CNT-WEB", "apps/web/api.ts"),
        (cmp_api, "CNT-API", "services/api/routes.py"),
    ):
        d = parse_spec_file(p_cmp)
        d.frontmatter["status"] = "active"
        d.frontmatter["container"] = parent_cnt
        d.frontmatter["source_path"] = spath
        p_cmp.write_text(dump_spec_document(d.frontmatter, d.body), encoding="utf-8")

    # OpenAPI YAML contract on CNT-PAYMENTS
    (repo / "services" / "payments").mkdir(parents=True)
    (repo / "services" / "payments" / "main.py").write_text("def serve():\n    return True\n", encoding="utf-8")
    (repo / "services" / "payments" / "openapi.yaml").write_text(
        "openapi: '3.1.0'\n"
        "servers:\n"
        "  - url: /pay-api\n"
        "paths:\n"
        "  /charges/{charge_id}:\n"
        "    post:\n"
        "      summary: Capture charge\n",
        encoding="utf-8",
    )
    d_oa = parse_spec_file(ctr_openapi)
    d_oa.frontmatter["status"] = "active"
    d_oa.frontmatter["protocol"] = "openapi"
    d_oa.frontmatter["container"] = "CNT-PAYMENTS"
    d_oa.frontmatter["specification_file"] = "services/payments/openapi.yaml"
    ctr_openapi.write_text(dump_spec_document(d_oa.frontmatter, d_oa.body), encoding="utf-8")

    # http_json AsciiDoc table contract on CNT-API (whose specification_file is a .py source file!)
    d_ev = parse_spec_file(ctr_events)
    d_ev.frontmatter["status"] = "active"
    d_ev.frontmatter["protocol"] = "http_json"
    d_ev.frontmatter["container"] = "CNT-API"
    d_ev.frontmatter["specification_file"] = "services/api/routes.py"
    ev_body = (
        "= Contract: Events HTTP JSON\n\n"
        "|===\n"
        "| Method | Path | Summary\n"
        "| `GET` | `/api/events` | Stream live events\n"
        "|===\n"
    )
    ctr_events.write_text(dump_spec_document(d_ev.frontmatter, ev_body), encoding="utf-8")

    # Backend FastAPI routes: includes unique /api/v2/catalog/{item_id} and ambiguous /v1/dup & /v2/dup
    (repo / "services" / "api").mkdir(parents=True)
    (repo / "services" / "api" / "routes.py").write_text(
        'from fastapi import FastAPI\n'
        'app = FastAPI()\n'
        '@app.get("/api/orders/{order_id}")\n'
        'def get_order(order_id: str):\n'
        '    return {}\n'
        '@app.get("/api/v2/catalog/{item_id}")\n'
        'def get_catalog_item(item_id: str):\n'
        '    return {}\n'
        '@app.get("/v1/dup/{id}")\n'
        'def dup_v1(id: str):\n'
        '    return {}\n'
        '@app.get("/v2/dup/{id}")\n'
        'def dup_v2(id: str):\n'
        '    return {}\n'
        '@app.delete("/test-only/nuke")\n'
        'def nuke():\n'
        '    return {}\n',
        encoding="utf-8",
    )

    # CMP-WEB-API-CLIENT calls exact /api/orders/${id}, prefix-omitted /catalog/${id}, OpenAPI /pay-api/charges/${id},
    # and ambiguous /dup/${id} (which must NOT guess between /v1/dup and /v2/dup)
    (repo / "apps" / "web").mkdir(parents=True)
    (repo / "apps" / "web" / "api.ts").write_text(
        'export async function fetchOrder(id: string) {\n'
        '  await fetch(`/api/orders/${id}`);\n'
        '  await fetch("/api/orders/ord_literal_42");\n'
        '  await fetch(`/catalog/${id}`);\n'
        '  await fetch(`/pay-api/charges/${id}`, { method: "POST" });\n'
        '  await fetch(`/dup/${id}`);\n'
        '}\n',
        encoding="utf-8",
    )

    # Container-direct CNT-SPA (no CMP-* child) subscribes to /api/events
    (repo / "apps" / "spa").mkdir(parents=True)
    (repo / "apps" / "spa" / "main.tsx").write_text(
        'export function connectStream() {\n'
        '  return new window.EventSource("/api/events");\n'
        '}\n',
        encoding="utf-8",
    )

    # Automated test file calling DELETE /test-only/nuke MUST be ignored (is_test = 1)
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_e2e_http.py").write_text(
        'import requests\n'
        'def test_nuke_endpoint():\n'
        '    requests.delete("http://localhost:8000/test-only/nuke")\n',
        encoding="utf-8",
    )

    idx = ArchitecturalIndex(repo_root=repo, eidos_dir=eidos_dir)
    try:
        idx.sync(force=True, auto_semantic=False)
        conn = idx._get_connection()

        edges = {
            (r["source_id"], r["target_id"], r["rel_type"]): (r["provenance"], r["evidence"])
            for r in conn.execute("SELECT source_id, target_id, rel_type, provenance, evidence FROM edges;").fetchall()
        }
        # CMP -> CMP ast_http link with file:line evidence
        assert ("CMP-WEB-API-CLIENT", "CMP-ORDER-CONTROLLER", "consumes_api") in edges
        prov_cmp, ev_cmp = edges[("CMP-WEB-API-CLIENT", "CMP-ORDER-CONTROLLER", "consumes_api")]
        assert prov_cmp == "ast_http"
        assert "apps/web/api.ts:2 -> GET /api/orders/{*}" in ev_cmp

        # CNT -> CNT ast_http links (including OpenAPI contract target & container-direct SPA client)
        assert edges[("CNT-WEB", "CNT-API", "depends_on")][0] == "ast_http"
        assert edges[("CNT-WEB", "CNT-PAYMENTS", "depends_on")][0] == "ast_http"
        assert edges[("CMP-WEB-API-CLIENT", "CTR-PAYMENTS-OPENAPI", "consumes")][0] == "ast_http"
        assert edges[("CNT-SPA", "CNT-API", "depends_on")][0] == "ast_http"
        assert edges[("CNT-SPA", "CTR-EVENTS-JSON", "consumes")][0] == "ast_http"

        # Single-candidate prefix disambiguation recorded /catalog/{*} -> /api/v2/catalog/{*},
        # while multi-candidate /dup/{*} and test-only /test-only/nuke were NOT linked
        couplings = [
            dict(r)
            for r in conn.execute("SELECT * FROM code_couplings ORDER BY src_file, line_start;").fetchall()
        ]
        target_refs = [c["target_ref"] for c in couplings]
        assert any("GET /api/orders/{*}" in tr for tr in target_refs)
        assert any("GET /api/v2/catalog/{*}" in tr for tr in target_refs)
        assert any("POST /pay-api/charges/{*}" in tr for tr in target_refs)
        assert any("GET /api/events" in tr for tr in target_refs)
        assert not any("/dup/" in tr for tr in target_refs)
        assert not any("nuke" in tr for tr in target_refs)

        # Symbol & repo-map hygiene: http_endpoint and http_request never inflate method counts or repo-map
        st = idx.get_index_status()
        assert st["ast"]["methods"] == 0
        assert st["ast"]["symbols_by_kind"].get("http_endpoint", 0) >= 5
        assert st["ast"]["symbols_by_kind"].get("http_request", 0) >= 5
        repo_map = idx.get_repo_map(max_tokens=1000)
        assert "GET /api/orders/{*}" not in repo_map

        # find-symbol locates HTTP endpoints with ast_symbol provenance and observed confidence
        sym_hits = idx.find_symbol("/api/orders")
        assert any(
            h["kind"] == "http_endpoint" and h["provenance"] == "ast_symbol" and h["confidence"] == "observed"
            for h in sym_hits
        )
    finally:
        idx.close()
