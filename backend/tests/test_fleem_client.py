from __future__ import annotations

from typing import Any

from backend.fleem_client import (
    FLEEM_GRAPHQL_URL,
    fetch_platform_snapshot,
    fetch_platform_snapshot_from_browser_page,
)


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: int) -> FakeResponse:
        self.calls.append(
            {
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return self._responses.pop(0)


def _page(current_page: int, total: int, platforms: list[dict[str, Any]]) -> FakeResponse:
    return FakeResponse(
        {
            "data": {
                "Fleem_queryPlatforms": {
                    "page": {
                        "currentPage": current_page,
                        "pageSize": 2,
                        "total": total,
                    },
                    "platforms": platforms,
                }
            }
        }
    )


def test_fetch_platform_snapshot_parses_current_view_and_collects_all_pages() -> None:
    session = FakeSession(
        [
            _page(1, 3, [{"_id": "one"}, {"_id": "two"}]),
            _page(2, 3, [{"_id": "three"}]),
        ]
    )

    snapshot = fetch_platform_snapshot(
        bearer_token="test-bearer-token",
        view_url=(
            "https://integrate.bmwgroup.net/fleem/Platforms?"
            "iql=ids.fleet+in+%28%22DTSV_China_C%22%2C+%22DTSV_China_R%22%29&"
            "platformStatus=Active"
        ),
        session=session,
        page_size=2,
    )

    assert snapshot["query"] == 'ids.fleet in ("DTSV_China_C", "DTSV_China_R")'
    assert snapshot["platform_status"] == ["Active"]
    assert snapshot["view"] == "platform-EEK_Export"
    assert snapshot["total"] == 3
    assert snapshot["platforms"] == [{"_id": "one"}, {"_id": "two"}, {"_id": "three"}]

    assert [call["url"] for call in session.calls] == [FLEEM_GRAPHQL_URL, FLEEM_GRAPHQL_URL]
    assert [call["json"]["variables"]["pagination"]["currentPage"] for call in session.calls] == [1, 2]
    assert session.calls[0]["json"]["variables"] == {
        "query": 'ids.fleet in ("DTSV_China_C", "DTSV_China_R")',
        "platformStatus": ["Active"],
        "timezoneOffset": "+08:00",
        "pagination": {"currentPage": 1, "pageSize": 2},
        "view": "platform-EEK_Export",
    }
    assert session.calls[0]["headers"] == {
        "Authorization": "Bearer test-bearer-token",
        "Apollo-Require-Preflight": "true",
        "Content-Type": "application/json",
        "integrate_path_context": "fleem",
    }


def test_fetch_platform_snapshot_from_browser_page_keeps_bearer_token_in_browser() -> None:
    class FakePage:
        def __init__(self) -> None:
            self.script = ""
            self.arguments: dict[str, Any] = {}

        def evaluate(self, script: str, arguments: dict[str, Any]) -> dict[str, Any]:
            self.script = script
            self.arguments = arguments
            return {
                "page": {"currentPage": 1, "pageSize": 100, "total": 1},
                "platforms": [{"_id": "one", "ids": {"vin": "VIN-1"}}],
            }

    page = FakePage()

    snapshot = fetch_platform_snapshot_from_browser_page(
        page=page,
        view_url=(
            "https://integrate.bmwgroup.net/fleem/Platforms?"
            "iql=ids.fleet+in+%28%22DTSV_China_C%22%2C+%22DTSV_China_R%22%29&"
            "platformStatus=Active"
        ),
    )

    assert snapshot["total"] == 1
    assert snapshot["platforms"] == [{"_id": "one", "ids": {"vin": "VIN-1"}}]
    assert page.arguments == {
        "query": 'ids.fleet in ("DTSV_China_C", "DTSV_China_R")',
        "platformStatus": ["Active"],
        "timezoneOffset": "+08:00",
        "pageSize": 100,
        "view": "platform-EEK_Export",
    }
    assert "localStorage.getItem('token')" in page.script
    assert "Authorization" in page.script


def test_fetch_platform_snapshot_includes_project_market_classification_from_vin() -> None:
    session = FakeSession(
        [
            _page(
                1,
                2,
                [
                    {"_id": "hong-kong", "ids": {"vin": "WMW12GC04RTA10044"}},
                    {"_id": "unmapped", "ids": {"vin": "UNKNOWN-VIN"}},
                ],
            )
        ]
    )

    snapshot = fetch_platform_snapshot(
        bearer_token="test-bearer-token",
        view_url=(
            "https://integrate.bmwgroup.net/fleem/Platforms?"
            "iql=ids.fleet+in+%28%22DTSV_China_C%22%2C+%22DTSV_China_R%22%29&"
            "platformStatus=Active"
        ),
        session=session,
        page_size=2,
    )

    assert snapshot["project_market_classification"] == {
        "source": "backend/analytics/assets/data/vin_project_mapping.json",
        "rule": "First VIN prefix match in the project mapping asset.",
        "market_counts": {"HK": 1, "UNMAPPED": 1},
        "platforms": [
            {
                "platform_id": "hong-kong",
                "vin": "WMW12GC04RTA10044",
                "market": "HK",
                "matched_vin_prefix": "WMW12GC04RTA10044",
            },
            {
                "platform_id": "unmapped",
                "vin": "UNKNOWN-VIN",
                "market": "",
                "matched_vin_prefix": "",
            },
        ],
    }
