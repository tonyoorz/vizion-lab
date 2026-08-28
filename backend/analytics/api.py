from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.analytics.auth_routes import router as auth_router
from backend.analytics.config import get_analytics_db_path, get_full_picture_hot_db_path
from backend.analytics.dashboard_snapshot import read_active_snapshot_state
from backend.analytics.read_models import (
    FullPictureDashboardDataError,
    FullPictureDashboardRequestError,
    build_defect_high_frequency_analysis_payload,
    build_defect_test_correlation,
    build_filter_metadata,
    build_full_picture_payload,
    build_full_picture_summary_payload,
    build_long_runner_analysis_payload,
    build_top_issue_analysis_payload,
    build_testing_summary,
    list_full_picture_ticket_rows,
    list_runs,
    list_testcases,
)
from backend.analytics.defect_query_models import build_defect_aggregate_payload
from backend.analytics.fallback_query import build_analytics_fallback_query_payload
from backend.analytics.ontology import load_ontology, OntologyLoadError
from backend.analytics.ontology_context import build_ontology_catalog_payload, build_test_case_context_payload
from backend.analytics.octane_field_catalog import load_local_octane_field_catalog, search_octane_fields
from backend.analytics.semantic_query import (
    SemanticQueryError,
    execute_semantic_query,
    execute_semantic_records,
)
from backend.analytics.agent_actor_capability import (
    ActorCapabilityError,
    enforce_defect_query_scope,
    verify_actor_capability_header,
)
from backend.analytics.qgate_weekly_report import build_qgate_weekly_report_payload
from backend.analytics.testing_coverage_models import (
    TestingCoverageDataNotReadyError,
    build_aida_status_rows,
    build_project_status_rows,
    build_test_team_analysis_payload,
    build_testing_coverage_filters,
    build_testcase_detail_rows,
)
from backend.analytics.traceability_models import build_traceability_analysis_payload
from backend.analytics.schema import ensure_schema


@asynccontextmanager
async def analytics_lifespan(_app: FastAPI) -> AsyncIterator[None]:
    ensure_schema(get_analytics_db_path())
    yield


app = FastAPI(title="Vizion Analytics API", lifespan=analytics_lifespan)

# 前端 dev server (8080) → analytics API (3003) 跨端口，需允许带 cookie
# 允许 localhost、127.0.0.1、本机 hostname 和本机 IP，让内网其他用户也能访问
import socket as _socket

_vizion_web_port = __import__("os").environ.get("VIZION_WEB_PORT", "8080")


def _detect_local_origins(port: str) -> list[str]:
    origins = [f"http://localhost:{port}", f"http://127.0.0.1:{port}"]
    try:
        hostname = _socket.gethostname()
        origins.append(f"http://{hostname}:{port}")
    except Exception:
        pass
    # 探测本机所有网卡的 IP
    try:
        hostname = _socket.gethostname()
        for info in _socket.getaddrinfo(hostname, None):
            ip = info[4][0]
            if ":" not in ip and ip not in ("127.0.0.1",):  # 只要 IPv4，排除 loopback
                origins.append(f"http://{ip}:{port}")
    except Exception:
        pass
    return origins


_cors_origins = _detect_local_origins(_vizion_web_port)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 登录/鉴权路由（无需 session 即可访问）
app.include_router(auth_router)


def _full_picture_query_params(request: Request) -> dict[str, object]:
    aggregated: dict[str, object] = {}
    for key, value in request.query_params.multi_items():
        existing = aggregated.get(key)
        if existing is None:
            aggregated[key] = value
        elif isinstance(existing, list):
            existing.append(value)
        else:
            aggregated[key] = [existing, value]
    return aggregated


@app.get("/health")
def health() -> dict[str, object]:
    return {"ok": True, "service": "analytics"}


@app.get("/api/full-picture/dashboard")
def full_picture_dashboard(request: Request) -> JSONResponse:
    try:
        payload = build_full_picture_payload(**_full_picture_query_params(request))
    except FullPictureDashboardDataError:
        return JSONResponse(
            status_code=503,
            content={"error": "analytics database not initialized"},
        )
    return JSONResponse(status_code=200, content=payload)


@app.get("/api/full-picture/dashboard/summary")
def full_picture_dashboard_summary(request: Request) -> JSONResponse:
    try:
        payload = build_full_picture_summary_payload(**_full_picture_query_params(request))
    except FullPictureDashboardDataError:
        return JSONResponse(
            status_code=503,
            content={"error": "analytics database not initialized"},
        )
    except FullPictureDashboardRequestError as exc:
        status_code = 409 if "snapshot" in str(exc).lower() else 400
        return JSONResponse(status_code=status_code, content={"error": str(exc)})
    return JSONResponse(status_code=200, content=payload)


@app.get("/api/full-picture/dashboard/tickets")
def full_picture_dashboard_tickets(request: Request) -> JSONResponse:
    try:
        payload = list_full_picture_ticket_rows(**_full_picture_query_params(request))
    except FullPictureDashboardDataError:
        return JSONResponse(
            status_code=503,
            content={"error": "analytics database not initialized"},
        )
    except FullPictureDashboardRequestError as exc:
        status_code = 409 if "snapshot" in str(exc).lower() else 400
        return JSONResponse(status_code=status_code, content={"error": str(exc)})
    return JSONResponse(status_code=200, content=payload)


@app.get("/api/full-picture/top-issue-analysis")
def full_picture_top_issue_analysis(request: Request) -> JSONResponse:
    try:
        payload = build_top_issue_analysis_payload(**_full_picture_query_params(request))
    except FullPictureDashboardDataError:
        return JSONResponse(
            status_code=503,
            content={"error": "analytics database not initialized"},
        )
    except FullPictureDashboardRequestError as exc:
        status_code = 409 if "snapshot" in str(exc).lower() else 400
        return JSONResponse(status_code=status_code, content={"error": str(exc)})
    return JSONResponse(status_code=200, content=payload)


@app.get("/api/full-picture/long-runner-analysis")
def full_picture_long_runner_analysis(request: Request) -> JSONResponse:
    try:
        payload = build_long_runner_analysis_payload(**_full_picture_query_params(request))
    except FullPictureDashboardDataError:
        return JSONResponse(
            status_code=503,
            content={"error": "analytics database not initialized"},
        )
    except FullPictureDashboardRequestError as exc:
        status_code = 409 if "snapshot" in str(exc).lower() else 400
        return JSONResponse(status_code=status_code, content={"error": str(exc)})
    return JSONResponse(status_code=200, content=payload)


@app.get("/api/full-picture/defect-high-frequency-analysis")
def full_picture_defect_high_frequency_analysis(request: Request) -> JSONResponse:
    try:
        payload = build_defect_high_frequency_analysis_payload(**_full_picture_query_params(request))
    except FullPictureDashboardDataError:
        return JSONResponse(
            status_code=503,
            content={"error": "analytics database not initialized"},
        )
    except FullPictureDashboardRequestError as exc:
        status_code = 409 if "snapshot" in str(exc).lower() else 400
        return JSONResponse(status_code=status_code, content={"error": str(exc)})
    return JSONResponse(status_code=200, content=payload)


@app.get("/api/full-picture/dashboard/refresh-status")
def full_picture_dashboard_refresh_status() -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content=read_active_snapshot_state(get_full_picture_hot_db_path()),
    )


@app.get("/api/testing/summary")
def testing_summary() -> dict[str, int]:
    return build_testing_summary()


@app.get("/api/testing/testcases")
def testing_testcases() -> list[dict[str, object]]:
    return list_testcases()


@app.get("/api/testing/runs")
def testing_runs() -> list[dict[str, object]]:
    return list_runs()


@app.get("/api/testing/team-analysis")
def testing_team_analysis(request: Request, team: str = "DTSV_China") -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content=build_test_team_analysis_payload(team, request.query_params),
    )


@app.get("/api/qgate-reports/weekly-report")
def qgate_weekly_report(year: str = "") -> JSONResponse:
    return JSONResponse(status_code=200, content=build_qgate_weekly_report_payload(year=year))


def _testing_coverage_not_ready_response(exc: TestingCoverageDataNotReadyError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": "testing coverage analysis data not ready",
            "missing_fields": exc.missing_fields,
        },
    )


@app.get("/api/testing/coverage-analysis/filters")
def testing_coverage_filters(request: Request) -> JSONResponse:
    try:
        payload = build_testing_coverage_filters(request.query_params)
    except TestingCoverageDataNotReadyError as exc:
        return _testing_coverage_not_ready_response(exc)
    return JSONResponse(status_code=200, content=payload)


@app.get("/api/testing/coverage-analysis/project-status")
def testing_coverage_project_status(request: Request) -> JSONResponse:
    try:
        payload = build_project_status_rows(request.query_params)
    except TestingCoverageDataNotReadyError as exc:
        return _testing_coverage_not_ready_response(exc)
    return JSONResponse(status_code=200, content=payload)


@app.get("/api/testing/coverage-analysis/aida-status")
def testing_coverage_aida_status(request: Request) -> JSONResponse:
    try:
        payload = build_aida_status_rows(request.query_params)
    except TestingCoverageDataNotReadyError as exc:
        return _testing_coverage_not_ready_response(exc)
    return JSONResponse(status_code=200, content=payload)


@app.get("/api/testing/coverage-analysis/testcase-detail")
def testing_coverage_testcase_detail(request: Request) -> JSONResponse:
    try:
        payload = build_testcase_detail_rows(request.query_params)
    except TestingCoverageDataNotReadyError as exc:
        return _testing_coverage_not_ready_response(exc)
    return JSONResponse(status_code=200, content=payload)


@app.get("/api/testing/traceability-analysis")
def testing_traceability_analysis(request: Request) -> JSONResponse:
    return JSONResponse(status_code=200, content=build_traceability_analysis_payload(request.query_params))


@app.get("/api/metadata/filters")
def metadata_filters() -> dict[str, list[str]]:
    return build_filter_metadata()


@app.get("/api/correlation/defect-test")
def defect_test_correlation(defect_id: str = Query(...)) -> dict[str, object]:
    return build_defect_test_correlation(defect_id)


# ---------------------------------------------------------------------------
# Governed agent endpoints — ontology catalog, semantic query/records,
# agent analytics aggregate, and analytics fallback query.
# These wrap already-implemented Python functions that were never registered
# as FastAPI routes.
# ---------------------------------------------------------------------------


@app.get("/api/ontology/catalog")
def ontology_catalog() -> JSONResponse:
    try:
        payload = build_ontology_catalog_payload()
    except FullPictureDashboardDataError:
        return JSONResponse(
            status_code=503,
            content={"error": "ontology catalog unavailable: analytics data not initialized"},
        )
    return JSONResponse(status_code=200, content=payload)


@app.post("/api/ontology/fields/search")
async def ontology_fields_search(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        body = {}
    query = str(body.get("query", "")).strip()
    if not query:
        return JSONResponse(status_code=400, content={"error": "query is required"})
    entity = body.get("entity")
    top_k = max(1, min(50, int(body.get("top_k", 10))))
    try:
        catalog = load_local_octane_field_catalog()
        results = search_octane_fields(catalog, query, entity=entity, top_k=top_k)
    except FullPictureDashboardDataError as exc:
        return JSONResponse(status_code=503, content={"error": str(exc)})
    return JSONResponse(
        status_code=200,
        content={"summary": catalog.get("summary", {}), "results": results},
    )


@app.post("/api/ontology/context")
async def ontology_context(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        body = None
    payload = build_test_case_context_payload(body)
    return JSONResponse(status_code=200, content=payload)


@app.post("/api/semantic/query")
async def semantic_query(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        catalog = load_ontology()
        payload = execute_semantic_query(body, catalog=catalog)
    except SemanticQueryError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.code,
                "safeMessage": "semantic query rejected",
                "retryable": False,
                "ruleCodes": exc.rule_codes,
            },
        )
    except OntologyLoadError as exc:
        return JSONResponse(
            status_code=503,
            content={"code": str(exc), "safeMessage": "ontology not available", "retryable": False},
        )
    return JSONResponse(status_code=200, content=payload)


@app.post("/api/semantic/records")
async def semantic_records(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        catalog = load_ontology()
        payload = execute_semantic_records(body, catalog=catalog)
    except SemanticQueryError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.code,
                "safeMessage": "semantic records query rejected",
                "retryable": False,
                "ruleCodes": exc.rule_codes,
            },
        )
    except OntologyLoadError as exc:
        return JSONResponse(
            status_code=503,
            content={"code": str(exc), "safeMessage": "ontology not available", "retryable": False},
        )
    return JSONResponse(status_code=200, content=payload)


@app.post("/api/agent/analytics/defects/aggregate")
async def agent_defect_aggregate(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "AGENT_DEFECT_QUERY_INVALID", "safeMessage": "invalid request body"},
        )
    try:
        actor = verify_actor_capability_header(request.headers)
        scoped_body = enforce_defect_query_scope(body, actor)
        payload = build_defect_aggregate_payload(**scoped_body)
    except ActorCapabilityError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"ok": False, "error": exc.code, "safeMessage": "agent scope denied"},
        )
    except FullPictureDashboardDataError as exc:
        return JSONResponse(status_code=503, content={"ok": False, "error": str(exc)})
    except FullPictureDashboardRequestError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})
    return JSONResponse(status_code=200, content=payload)


@app.post("/api/analytics/fallback/query")
async def analytics_fallback_query(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        payload = build_analytics_fallback_query_payload(**body)
    except FullPictureDashboardDataError as exc:
        return JSONResponse(status_code=503, content={"ok": False, "error": str(exc)})
    except FullPictureDashboardRequestError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})
    return JSONResponse(status_code=200, content=payload)
