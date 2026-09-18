from __future__ import annotations

import time
from typing import Any

from fastapi.testclient import TestClient

from backend.analytics import api
from backend.analytics.agent_actor_capability import ACTOR_CAPABILITY_HEADER, create_actor_capability


TEST_SECRET = "agent-analytics-route-test-secret"


def _actor_headers(monkeypatch) -> dict[str, str]:
    monkeypatch.setenv("VIZION_AGENT_ACTOR_CAPABILITY_SECRET", TEST_SECRET)
    actor = {
        "actorId": "alice",
        "scopeHash": "scope-alice",
        "scopes": {
            "teamIds": ["DTSV_China"],
            "projectIds": ["IDCEVO"],
            "allowedObjectTypes": ["quality.defect"],
        },
    }
    token = create_actor_capability(
        actor,
        secret=TEST_SECRET,
        now=int(time.time()),
        nonce="agent-analytics-route-test-nonce",
    )
    return {ACTOR_CAPABILITY_HEADER: token}


def test_agent_defect_aggregate_requires_actor_capability() -> None:
    response = TestClient(api.app).post("/api/agent/analytics/defects/aggregate", json={})

    assert response.status_code == 401
    assert response.json()["code"] == "ACTOR_CAPABILITY_MISSING"


def test_agent_defect_records_requires_actor_capability() -> None:
    response = TestClient(api.app).post("/api/agent/analytics/defects/records", json={})

    assert response.status_code == 401
    assert response.json()["code"] == "ACTOR_CAPABILITY_MISSING"


def test_agent_defect_export_requires_actor_capability() -> None:
    response = TestClient(api.app).post("/api/agent/analytics/defects/export", json={})

    assert response.status_code == 401
    assert response.json()["code"] == "ACTOR_CAPABILITY_MISSING"


def test_agent_defect_export_returns_a_short_lived_download_url(monkeypatch) -> None:
    captured_payload: dict[str, Any] = {}

    def create_export(**payload: Any) -> dict[str, Any]:
        captured_payload.update(payload)
        return {
            "schema_version": "1.0",
            "snapshot_version": "snapshot-1",
            "query_fingerprint": "fingerprint-1",
            "download_token": "download-token",
            "expires_at": 1_800_000_000,
        }

    monkeypatch.setattr(api, "create_defect_records_export_payload", create_export, raising=False)

    response = TestClient(api.app).post(
        "/api/agent/analytics/defects/export",
        json={"drilldown_ref": "ref-1"},
        headers=_actor_headers(monkeypatch),
    )

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "1.0",
        "snapshot_version": "snapshot-1",
        "query_fingerprint": "fingerprint-1",
        "expires_at": 1_800_000_000,
        "download_url": "/api/agent/analytics/defects/export/download?token=download-token",
    }
    assert captured_payload["drilldown_ref"] == "ref-1"
    assert captured_payload["agent_actor_scope_hash"] == "scope-alice"
    assert "agent_drilldown_secret" in captured_payload


def test_agent_defect_export_download_streams_csv(monkeypatch) -> None:
    monkeypatch.setenv("VIZION_AGENT_ACTOR_CAPABILITY_SECRET", TEST_SECRET)

    def stream_export(token: str, *, secret: str) -> tuple[str, list[str]]:
        assert token == "download-token"
        assert secret == TEST_SECRET
        return "defect-records-fingerprint-1.csv", ["ticket_id,ticket_name\r\n", "D-1,First\r\n"]

    monkeypatch.setattr(api, "stream_defect_records_export_csv", stream_export, raising=False)

    response = TestClient(api.app).get("/api/agent/analytics/defects/export/download?token=download-token")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == 'attachment; filename="defect-records-fingerprint-1.csv"'
    assert response.text == "ticket_id,ticket_name\r\nD-1,First\r\n"


def test_agent_defect_aggregate_injects_signed_actor_scope(monkeypatch) -> None:
    captured_payload: dict[str, Any] = {}

    def build_payload(**payload: Any) -> dict[str, Any]:
        captured_payload.update(payload)
        return {"ok": True}

    monkeypatch.setattr(api, "build_defect_aggregate_payload", build_payload, raising=False)

    response = TestClient(api.app).post(
        "/api/agent/analytics/defects/aggregate",
        json={"filters": {"years": ["2026"]}},
        headers=_actor_headers(monkeypatch),
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert captured_payload["filters"] == {
        "years": ["2026"],
        "problem_finder_teams": ["DTSV_China"],
        "projects": ["IDCEVO"],
    }
    assert captured_payload["agent_actor_scope_hash"] == "scope-alice"
    assert "agent_drilldown_secret" in captured_payload