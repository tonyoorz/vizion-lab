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