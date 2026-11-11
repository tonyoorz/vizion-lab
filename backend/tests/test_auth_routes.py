"""Focused authentication-route behavior tests."""
from __future__ import annotations

import sys
import types

from backend.analytics import auth_routes


def test_keeper_receives_mobile_push_authentication_arguments(monkeypatch):
    captured: dict[str, object] = {}
    expected_session = object()

    def get_or_create_session(**kwargs):
        captured.update(kwargs)
        return expected_session

    client_module = types.ModuleType("session_keeper.client")
    client_module.get_or_create_session = get_or_create_session
    package_module = types.ModuleType("session_keeper")
    package_module.client = client_module
    monkeypatch.setitem(sys.modules, "session_keeper", package_module)
    monkeypatch.setitem(sys.modules, "session_keeper.client", client_module)

    session = auth_routes._keeper_get_or_create_session(
        "q123456",
        "strong-pin",
        strong_auth=True,
        strong_auth_type="mobile",
    )

    assert session is expected_session
    assert captured == {
        "tool_key": "octane",
        "username": "q123456",
        "password": "strong-pin",
        "strong_auth": True,
        "strong_auth_type": "mobile",
    }


def test_mobile_push_login_uses_session_keeper(monkeypatch):
    expected_session = object()
    captured: dict[str, object] = {}

    def keeper_session(username: str, password: str, **kwargs):
        captured["username"] = username
        captured["password"] = password
        captured.update(kwargs)
        return expected_session

    monkeypatch.setattr(auth_routes, "_keeper_get_or_create_session", keeper_session)

    session = auth_routes._get_or_create_login_session(
        "q123456",
        "strong-pin",
        strong_auth=True,
        strong_auth_type="mobile",
    )

    assert session is expected_session
    assert not hasattr(auth_routes, "_browser_get_or_create_session")
    assert captured == {
        "username": "q123456",
        "password": "strong-pin",
        "strong_auth": True,
        "strong_auth_type": "mobile",
    }