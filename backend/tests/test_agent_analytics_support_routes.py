from __future__ import annotations

import time
from typing import Any

from fastapi.testclient import TestClient

from backend.analytics import api
from backend.analytics.agent_actor_capability import ACTOR_CAPABILITY_HEADER, create_actor_capability


TEST_SECRET = "agent-analytics-support-route-test-secret"


def _actor_headers(monkeypatch) -> dict[str, str]:
    monkeypatch.setenv("VIZION_AGENT_ACTOR_CAPABILITY_SECRET", TEST_SECRET)
    actor = {
        "actorId": "alice",
        "scopeHash": "scope-alice",
        "scopes": {
            "teamIds": ["DTSV_China"],
            "allowedObjectTypes": ["quality.defect"],
        },
    }
    token = create_actor_capability(
        actor,
        secret=TEST_SECRET,
        now=int(time.time()),
        nonce="agent-analytics-support-route-test-nonce",
    )
    return {ACTOR_CAPABILITY_HEADER: token}


def test_analytics_fallback_route_rejects_raw_sql() -> None:
    response = TestClient(api.app).post("/api/analytics/fallback/query", json={"sql": "SELECT 1"})

    assert response.status_code == 400
    assert "does not accept raw SQL" in response.json()["error"]


def test_filter_value_search_route_forwards_structured_payload(monkeypatch) -> None:
    captured_payload: dict[str, Any] = {}

    def build_payload(**payload: Any) -> dict[str, Any]:
        captured_payload.update(payload)
        return {"values": [{"value": "Alice", "count": 2}]}

    monkeypatch.setattr(api, "build_analytics_filter_value_search_payload", build_payload, raising=False)

    response = TestClient(api.app).post(
        "/api/analytics/filter-values/search",
        json={"dataset": "defects", "field": "detected_by", "query": "alice"},
    )

    assert response.status_code == 200
    assert response.json() == {"values": [{"value": "Alice", "count": 2}]}
    assert captured_payload == {"dataset": "defects", "field": "detected_by", "query": "alice"}


def test_filter_value_search_injects_verified_scope(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def build_payload(**payload: Any) -> dict[str, Any]:
        captured.update(payload)
        return {"values": []}

    monkeypatch.setattr(api, "build_analytics_filter_value_search_payload", build_payload, raising=False)
    response = TestClient(api.app).post(
        "/api/analytics/filter-values/search",
        json={"dataset": "defects", "field": "detected_by", "query": "alice"},
        headers=_actor_headers(monkeypatch),
    )

    assert response.status_code == 200
    assert captured["filters"] == {"problem_finder_teams": ["DTSV_China"]}


def test_filter_value_search_rejects_a_scope_expansion(monkeypatch) -> None:
    response = TestClient(api.app).post(
        "/api/analytics/filter-values/search",
        json={"dataset": "defects", "field": "detected_by", "filters": {"problem_finder_teams": ["Other"]}},
        headers=_actor_headers(monkeypatch),
    )

    assert response.status_code == 403
    assert response.json()["code"] == "ACTOR_CAPABILITY_SCOPE_DENIED"


def test_ontology_catalog_route_exposes_the_catalog_builder(monkeypatch) -> None:
    monkeypatch.setattr(api, "build_ontology_catalog_payload", lambda: {"ontology_version": "1.0"}, raising=False)

    response = TestClient(api.app).get("/api/ontology/catalog")

    assert response.status_code == 200
    assert response.json() == {"ontology_version": "1.0"}


def test_ontology_field_search_requires_a_query() -> None:
    response = TestClient(api.app).post("/api/ontology/fields/search", json={})

    assert response.status_code == 400
    assert response.json() == {"error": "query is required"}


def test_ontology_context_returns_a_safe_validation_error(monkeypatch) -> None:
    def build_context(_payload: dict[str, object]) -> dict[str, object]:
        raise ValueError("Ontology context anchor value is required")

    monkeypatch.setattr(api, "build_test_case_context_payload", build_context, raising=False)

    response = TestClient(api.app).post("/api/ontology/context", json={})

    assert response.status_code == 400
    assert response.json() == {"error": "Ontology context anchor value is required"}


def test_agent_team_fv_analysis_requires_actor_capability() -> None:
    response = TestClient(api.app).post("/api/agent/analytics/testing/team-fv-analysis", json={"team": "DTSV"})

    assert response.status_code == 401
    assert response.json()["code"] == "ACTOR_CAPABILITY_MISSING"


def test_agent_team_fv_analysis_normalizes_the_verified_team_scope(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def build_payload(team: str, query_params: dict[str, Any]) -> dict[str, bool]:
        captured["team"] = team
        captured["query_params"] = query_params
        return {"ok": True}

    monkeypatch.setattr(api, "build_test_team_analysis_payload", build_payload, raising=False)

    response = TestClient(api.app).post(
        "/api/agent/analytics/testing/team-fv-analysis",
        json={"team": "DTSV", "years": ["2026"]},
        headers=_actor_headers(monkeypatch),
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert captured == {
        "team": "DTSV_China",
        "query_params": {"years": ["2026"], "group_by": "fv"},
    }