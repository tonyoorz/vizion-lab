from fastapi.testclient import TestClient

from backend.analytics.api import app


def test_auth_status_route_is_registered():
    with TestClient(app) as client:
        response = client.get("/api/auth/me")

    assert response.status_code == 200
    assert response.json() == {"authenticated": False}