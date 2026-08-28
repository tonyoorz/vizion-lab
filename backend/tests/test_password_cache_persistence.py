"""Tests for the persistent (disk-backed) password cache in auth_routes.

Covers the scenario that caused the original 401: the analytics-api process
restarting and losing the in-memory password cache, so session_keeper could no
longer refresh expired Octane cookies.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.analytics import auth_routes
from backend.analytics.auth_routes import (
    _cache_password,
    _clear_cached_password,
    _get_cached_password,
    _load_password_cache,
    _persist_password_cache,
    _PASSWORD_CACHE_TTL,
)


@pytest.fixture(autouse=True)
def _isolate_cache(monkeypatch, tmp_path):
    """Point the cache file at a temp dir and start with an empty in-memory cache."""
    cache_file = tmp_path / "password_cache.json"
    monkeypatch.setattr(
        "backend.analytics.config.get_password_cache_file_path",
        lambda: cache_file,
    )
    monkeypatch.setattr(
        "backend.analytics.auth_routes.get_password_cache_file_path",
        lambda: cache_file,
    )
    saved = dict(auth_routes._password_cache)
    auth_routes._password_cache.clear()
    yield cache_file
    auth_routes._password_cache.clear()
    auth_routes._password_cache.update(saved)


def test_cache_password_round_trips_to_memory_and_disk():
    _cache_password("q123456", "s3cret")

    # memory
    assert _get_cached_password("q123456") == "s3cret"
    # disk
    assert _get_disk_cache()["q123456"]["enc"]  # non-empty ciphertext


def test_disk_cache_is_encrypted_not_plaintext():
    _cache_password("q123456", "s3cret")

    raw = _get_disk_cache()
    ciphertext = raw["q123456"]["enc"]
    assert "s3cret" not in ciphertext
    assert ciphertext  # non-empty


def test_password_survives_process_restart_via_reload():
    _cache_password("q123456", "s3cret")

    # Simulate process restart: wipe memory, then reload from disk
    auth_routes._password_cache.clear()
    assert _get_cached_password("q123456") is None

    _load_password_cache()
    assert _get_cached_password("q123456") == "s3cret"


def test_expired_entry_is_not_reloaded():
    _cache_password("q123456", "s3cret")

    # Tamper with the on-disk expiry to make it look old
    data = _get_disk_cache()
    data["q123456"]["exp"] = time.time() - 1
    _write_disk_cache(data)

    auth_routes._password_cache.clear()
    _load_password_cache()
    assert _get_cached_password("q123456") is None


def test_clear_password_removes_from_memory_and_disk():
    _cache_password("q123456", "s3cret")
    _clear_cached_password("q123456")

    assert _get_cached_password("q123456") is None
    assert "q123456" not in _get_disk_cache()


def test_ttl_is_at_least_24_hours():
    # The whole point of persistence is to survive longer than one workday.
    assert _PASSWORD_CACHE_TTL >= 24 * 3600


def test_multiple_users_are_isolated():
    _cache_password("q111111", "pw-a")
    _cache_password("q222222", "pw-b")

    assert _get_cached_password("q111111") == "pw-a"
    assert _get_cached_password("q222222") == "pw-b"

    # Reload — both survive
    auth_routes._password_cache.clear()
    _load_password_cache()
    assert _get_cached_password("q111111") == "pw-a"
    assert _get_cached_password("q222222") == "pw-b"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _get_disk_cache() -> dict:
    return json.loads(auth_routes.get_password_cache_file_path().read_text(encoding="utf-8"))


def _write_disk_cache(data: dict) -> None:
    auth_routes.get_password_cache_file_path().write_text(
        json.dumps(data), encoding="utf-8"
    )
