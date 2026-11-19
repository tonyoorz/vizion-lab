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


def test_reporter_names_load_only_requested_source_rows(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    import json
    import sqlite3

    source = tmp_path / "source.db"
    conn = sqlite3.connect(source)
    try:
        conn.execute("CREATE TABLE octane_defects (defect_id TEXT PRIMARY KEY, raw_json TEXT)")
        conn.executemany("INSERT INTO octane_defects VALUES (?, ?)", [
            ("D-1", json.dumps({"detected_by": {"full_name": "Alice"}, "author": {"full_name": "Other Author"}})),
            ("D-2", json.dumps({"author": {"full_name": "Bob"}})),
            ("D-3", json.dumps({"detected_by": {"full_name": "Not Requested"}})),
        ])
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(read_models, "get_full_picture_source_db_path", lambda: source)

    assert read_models._load_detected_by_names(["D-1", "D-2"]) == {"D-1": "Alice", "D-2": "Bob"}


def test_analytics_loader_applies_the_person_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.analytics import defect_query_models

    rows = [
        {"ticket_id": "D-1", "detected_by": "Alice"},
        {"ticket_id": "D-2", "detected_by": "Bob"},
        {"ticket_id": "D-3"},
    ]
    monkeypatch.setattr(read_models, "normalize_query", lambda **_filters: object())
    monkeypatch.setattr(read_models, "_load_materialized_ticket_rows", lambda **_kwargs: rows)
    monkeypatch.setattr(read_models, "_load_detected_by_names", lambda _ids: {"D-3": "Alice"}, raising=False)

    result = defect_query_models._load_rows("snapshot-1", {"detected_by": ["Alice"]}, {})

    assert [row["ticket_id"] for row in result] == ["D-1", "D-3"]


@pytest.mark.parametrize("lookup", [False, True])
@pytest.mark.parametrize("years", [[], ["2025"]])
def test_agent_queries_preserve_explicit_years_without_inheriting_dashboard_defaults(monkeypatch, lookup, years) -> None:
    from backend.analytics import defect_query_models

    captured: dict[str, object] = {}

    def load_rows(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(read_models, "_read_snapshot_metadata", lambda: {})
    monkeypatch.setattr(read_models, "_resolve_effective_snapshot_version", lambda _metadata: "snapshot-1")
    monkeypatch.setattr(read_models, "_load_materialized_ticket_rows", load_rows)
    filters = {"years": years} if years else {}
    if lookup:
        read_models.build_analytics_filter_value_search_payload(dataset="defects", field="detected_by", filters=filters)
    else:
        defect_query_models._load_rows("snapshot-1", filters, {})

    assert getattr(captured["query"], "years") == tuple(years)


def test_reporter_dimension_uses_materialized_and_source_names(monkeypatch) -> None:
    from backend.analytics import defect_query_models

    monkeypatch.setattr(defect_query_models, "_current_snapshot_version", lambda: "snapshot-1")
    monkeypatch.setattr(read_models, "_load_materialized_ticket_rows", lambda **_kwargs: [
        {"ticket_id": "D-1", "detected_by": "Alice"},
        {"ticket_id": "D-2", "detected_by": "Bob"},
        {"ticket_id": "D-3"},
    ])
    monkeypatch.setattr(read_models, "_load_detected_by_names", lambda _ids: {"D-3": "Alice"})

    result = defect_query_models.build_defect_aggregate_payload(dimensions=["detected_by"], filters={}, time={})

    assert {row["detected_by"]: row["defect_count"] for row in result["rows"]} == {"Alice": 2, "Bob": 1}