from __future__ import annotations

import pytest

from backend.analytics import read_models


def test_filter_value_search_counts_ranked_values_from_active_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    query = object()
    captured: dict[str, object] = {}
    ticket_rows = [
        {"detected_by": "Alice"},
        {"detected_by": "Alice"},
        {"detected_by": "Bob"},
    ]

    monkeypatch.setattr(read_models, "normalize_query", lambda **_filters: query)
    monkeypatch.setattr(read_models, "_read_snapshot_metadata", lambda: {"active_snapshot_version": "snapshot-1"})
    monkeypatch.setattr(read_models, "_resolve_effective_snapshot_version", lambda _metadata: "snapshot-1")

    def load_rows(**kwargs: object) -> list[dict[str, str]]:
        captured.update(kwargs)
        return ticket_rows

    monkeypatch.setattr(read_models, "_load_materialized_ticket_rows", load_rows)

    result = read_models.build_analytics_filter_value_search_payload(
        dataset="defects",
        field="detected_by",
        limit=1,
        filters={"years": ["2026"]},
    )

    assert captured == {"snapshot_version": "snapshot-1", "query": query}
    assert result == {
        "schema_version": "1.0",
        "dataset": "defects",
        "field": "detected_by",
        "query": "",
        "snapshot_version": "snapshot-1",
        "values": [{"value": "Alice", "count": 2}],
        "total_values": 2,
        "returned_values": 1,
        "truncated": True,
    }


def _patch_snapshot_readers(monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, str]]) -> None:
    monkeypatch.setattr(read_models, "normalize_query", lambda **_filters: object())
    monkeypatch.setattr(read_models, "_read_snapshot_metadata", lambda: {})
    monkeypatch.setattr(read_models, "_resolve_effective_snapshot_version", lambda _metadata: "snapshot-1")
    monkeypatch.setattr(read_models, "_load_materialized_ticket_rows", lambda **_kwargs: rows)


def test_filter_value_search_matches_query_case_insensitively(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_snapshot_readers(
        monkeypatch,
        [
            {"detected_by": "Size Li"},
            {"detected_by": "Size Li"},
            {"detected_by": "Alice"},
        ],
    )

    result = read_models.build_analytics_filter_value_search_payload(
        dataset="defects",
        field="detected_by",
        query="siz",
        limit=10,
    )

    assert result["query"] == "siz"
    assert result["values"] == [{"value": "Size Li", "count": 2}]
    assert result["total_values"] == 1
    assert result["returned_values"] == 1
    assert result["truncated"] is False


def test_filter_value_search_derives_business_module(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_snapshot_readers(
        monkeypatch,
        [
            {"solution_cluster": "Camera"},
            {"defect_category": "Camera"},
            {"solution_cluster": "", "defect_category": "Navigation"},
        ],
    )

    result = read_models.build_analytics_filter_value_search_payload(
        dataset="defects",
        field="business_module",
        query="cam",
        limit=5,
    )

    assert result["values"] == [{"value": "Camera", "count": 2}]
    assert result["total_values"] == 1


def test_filter_value_search_loads_missing_detected_by_names(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_ids: list[str] = []

    def load_names(ticket_ids: list[str]) -> dict[str, str]:
        captured_ids.extend(ticket_ids)
        return {"D-1": "Size Li"}

    _patch_snapshot_readers(
        monkeypatch,
        [
            {"ticket_id": "D-1"},
            {"ticket_id": "D-1", "detected_by": ""},
            {"ticket_id": "D-2", "detected_by": "Alice"},
        ],
    )
    monkeypatch.setattr(read_models, "_load_detected_by_names", load_names)

    result = read_models.build_analytics_filter_value_search_payload(
        dataset="defects",
        field="detected_by",
        query="siz",
        limit=5,
        filters={},
    )

    assert captured_ids == ["D-1"]
    assert result["values"] == [{"value": "Size Li", "count": 2}]


def test_filter_value_search_rejects_unsupported_field() -> None:
    with pytest.raises(read_models.FullPictureDashboardRequestError, match="Unsupported filter value field"):
        read_models.build_analytics_filter_value_search_payload(dataset="defects", field="status")