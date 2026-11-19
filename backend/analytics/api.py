from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlencode

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from backend.analytics.auth_routes import router as auth_router
from backend.analytics.agent_actor_capability import (
    ACTOR_CAPABILITY_HEADER,
    ActorCapabilityError,
    enforce_defect_query_scope,
    enforce_testing_team_fv_analysis_scope,
    get_agent_actor_capability_secret,
    verify_actor_capability_header,
)
from backend.analytics.config import get_analytics_db_path, get_full_picture_hot_db_path
from backend.analytics.dashboard_snapshot import read_active_snapshot_state
from backend.analytics.defect_query_models import (
    AgentDrilldownScopeError,
    build_defect_aggregate_payload,
    build_defect_records_payload,
    create_defect_records_export_payload,
    stream_defect_records_export_csv,
)
from backend.analytics.ontology import OntologyLoadError, load_ontology
from backend.analytics.ontology_context import build_ontology_catalog_payload, build_test_case_context_payload
from backend.analytics.octane_field_catalog import load_local_octane_field_catalog, search_octane_fields
from backend.analytics.fallback_query import build_analytics_fallback_query_payload
from backend.analytics.semantic_analysis_store import SemanticAnalysisStore
from backend.analytics.semantic_query import SemanticQueryError, execute_semantic_query, execute_semantic_records
from backend.analytics.read_models import (
    FullPictureDashboardDataError,
    FullPictureDashboardRequestError,
    build_analytics_filter_value_search_payload,
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


@app.get("/api/ontology/catalog")
def ontology_catalog() -> JSONResponse:
    try:
        return JSONResponse(status_code=200, content=build_ontology_catalog_payload())
    except (OntologyLoadError, sqlite3.Error, OSError):
        return JSONResponse(status_code=503, content={"error": "ontology catalog is unavailable"})


def _agent_capability_error_response(error: ActorCapabilityError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={"code": error.code, "safeMessage": "agent capability rejected", "retryable": False},
    )


def _agent_analytics_schema_error_response() -> JSONResponse:
    return JSONResponse(status_code=400, content={
        "code": "AGENT_ANALYTICS_SCHEMA_INVALID", "safeMessage": "agent analytics request rejected", "retryable": False,
    })


async def _agent_defect_response(request: Request, *, operation: str) -> JSONResponse:
    try:
        actor = verify_actor_capability_header(request.headers)
        raw_payload = await request.json()
        if operation == "aggregate":
            scoped_payload = enforce_defect_query_scope(raw_payload, actor)
        else:
            if not isinstance(raw_payload, dict) or {"agent_actor_scope_hash", "agent_drilldown_secret"} & set(raw_payload):
                raise ValueError("agent records payload must be an object")
            scoped_payload = raw_payload
        builder = {
            "aggregate": build_defect_aggregate_payload,
            "records": build_defect_records_payload,
            "export": create_defect_records_export_payload,
        }[operation]
        payload = await run_in_threadpool(
            builder, **scoped_payload,
            agent_actor_scope_hash=actor["scopeHash"],
            agent_drilldown_secret=get_agent_actor_capability_secret(),
        )
        if operation == "export":
            payload = dict(payload)
            token = payload.pop("download_token")
            payload["download_url"] = "/api/agent/analytics/defects/export/download?" + urlencode({"token": token})
        return JSONResponse(status_code=200, content=payload)
    except ActorCapabilityError as exc:
        return _agent_capability_error_response(exc)
    except AgentDrilldownScopeError:
        return JSONResponse(status_code=403, content={
            "code": "AGENT_DRILLDOWN_SCOPE_DENIED", "safeMessage": "agent drilldown denied", "retryable": False,
        })
    except (FullPictureDashboardDataError, sqlite3.Error, OSError):
        return JSONResponse(status_code=503, content={"error": "analytics database not initialized"})
    except (FullPictureDashboardRequestError, ValueError, TypeError):
        return _agent_analytics_schema_error_response()


@app.post("/api/agent/analytics/defects/aggregate")
async def agent_analytics_defects_aggregate(request: Request) -> JSONResponse:
    return await _agent_defect_response(request, operation="aggregate")


@app.post("/api/agent/analytics/defects/records")
async def agent_analytics_defects_records(request: Request) -> JSONResponse:
    return await _agent_defect_response(request, operation="records")


@app.post("/api/agent/analytics/defects/export")
async def agent_analytics_defects_export(request: Request) -> JSONResponse:
    return await _agent_defect_response(request, operation="export")


@app.get("/api/agent/analytics/defects/export/download")
def agent_analytics_defects_export_download(token: str = Query(...)) -> Response:
    try:
        filename, rows = stream_defect_records_export_csv(token, secret=get_agent_actor_capability_secret())
        return StreamingResponse(rows, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    except ActorCapabilityError as exc:
        return _agent_capability_error_response(exc)
    except AgentDrilldownScopeError:
        return JSONResponse(status_code=403, content={"code": "AGENT_DRILLDOWN_SCOPE_DENIED", "retryable": False})
    except (FullPictureDashboardDataError, sqlite3.Error, OSError):
        return JSONResponse(status_code=503, content={"error": "analytics database not initialized"})
    except (FullPictureDashboardRequestError, ValueError):
        return _agent_analytics_schema_error_response()


@app.post("/api/analytics/filter-values/search")
async def analytics_filter_values_search(request: Request) -> JSONResponse:
    try:
        raw_payload = await request.json()
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        if request.headers.get(ACTOR_CAPABILITY_HEADER) is not None:
            payload = enforce_defect_query_scope(payload, verify_actor_capability_header(request.headers))
        result = await run_in_threadpool(build_analytics_filter_value_search_payload, **payload)
        return JSONResponse(status_code=200, content=result)
    except ActorCapabilityError as exc:
        return _agent_capability_error_response(exc)
    except (FullPictureDashboardDataError, sqlite3.Error, OSError):
        return JSONResponse(status_code=503, content={"error": "analytics database not initialized"})
    except (FullPictureDashboardRequestError, ValueError, TypeError) as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})


@app.post("/api/analytics/fallback/query")
async def analytics_fallback_query(request: Request) -> JSONResponse:
    try:
        raw_payload = await request.json()
        payload = await run_in_threadpool(build_analytics_fallback_query_payload, **(raw_payload if isinstance(raw_payload, dict) else {}))
        return JSONResponse(status_code=200, content=payload)
    except (FullPictureDashboardDataError, sqlite3.Error, OSError):
        return JSONResponse(status_code=503, content={"error": "analytics database not initialized"})
    except (FullPictureDashboardRequestError, ValueError, TypeError) as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})


@app.post("/api/ontology/fields/search")
async def ontology_fields_search(request: Request) -> JSONResponse:
    try:
        raw_payload = await request.json()
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        query = str(payload.get("query") or "").strip()
        if not query:
            return JSONResponse(status_code=400, content={"error": "query is required"})
        top_k = max(1, min(50, int(payload.get("top_k") or 20)))
        entity = str(payload.get("entity") or "").strip() or None
        catalog = await run_in_threadpool(load_local_octane_field_catalog, sample_limit=200)
        results = await run_in_threadpool(search_octane_fields, catalog, query, entity=entity, top_k=top_k)
        return JSONResponse(status_code=200, content={"summary": catalog["summary"], "results": results, "source": catalog["source"]})
    except (ValueError, TypeError):
        return JSONResponse(status_code=400, content={"error": "invalid field search request"})
    except (sqlite3.Error, OSError):
        return JSONResponse(status_code=503, content={"error": "field catalog is unavailable"})


@app.post("/api/ontology/context")
async def ontology_context(request: Request) -> JSONResponse:
    try:
        raw_payload = await request.json()
        payload = await run_in_threadpool(build_test_case_context_payload, raw_payload if isinstance(raw_payload, dict) else {})
        return JSONResponse(status_code=200, content=payload)
    except (ValueError, TypeError) as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except (sqlite3.Error, OSError):
        return JSONResponse(status_code=503, content={"error": "testcase context is unavailable"})


@app.post("/api/agent/analytics/testing/team-fv-analysis")
async def agent_testing_team_fv_analysis(request: Request) -> JSONResponse:
    try:
        actor = verify_actor_capability_header(request.headers)
        scoped_payload = enforce_testing_team_fv_analysis_scope(await request.json(), actor)
        team = str(scoped_payload.pop("team"))
        payload = await run_in_threadpool(build_test_team_analysis_payload, team, {**scoped_payload, "group_by": "fv"})
        return JSONResponse(status_code=200, content=payload)
    except ActorCapabilityError as exc:
        return _agent_capability_error_response(exc)
    except (ValueError, TypeError):
        return _agent_analytics_schema_error_response()
    except (TestingCoverageDataNotReadyError, sqlite3.Error, OSError):
        return JSONResponse(status_code=503, content={"error": "testing analysis is unavailable"})


async def _semantic_response(request: Request, *, records: bool = False) -> JSONResponse:
    safe_message = "semantic records query rejected" if records else "semantic query rejected"
    try:
        try:
            payload = await request.json()
        except ValueError as exc:
            raise SemanticQueryError("SEMANTIC_REQUEST_INVALID_JSON") from exc
        catalog = load_ontology()
        execute = execute_semantic_records if records else execute_semantic_query
        result = await run_in_threadpool(execute, payload, catalog=catalog, analysis_store=SemanticAnalysisStore())
        return JSONResponse(status_code=200, content=result)
    except SemanticQueryError as exc:
        content: dict[str, object] = {"code": exc.code, "safeMessage": safe_message, "retryable": exc.status_code >= 500}
        if exc.rule_codes:
            content["ruleCodes"] = exc.rule_codes
        return JSONResponse(status_code=exc.status_code, content=content)
    except OntologyLoadError:
        return JSONResponse(status_code=503, content={"code": "SEMANTIC_ONTOLOGY_UNAVAILABLE", "safeMessage": safe_message, "retryable": False})
    except (sqlite3.Error, OSError):
        return JSONResponse(status_code=503, content={"code": "SEMANTIC_STORAGE_UNAVAILABLE", "safeMessage": safe_message, "retryable": True})


@app.post("/api/semantic/query")
async def semantic_query(request: Request) -> JSONResponse:
    return await _semantic_response(request)


@app.post("/api/semantic/records")
async def semantic_records(request: Request) -> JSONResponse:
    return await _semantic_response(request, records=True)


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