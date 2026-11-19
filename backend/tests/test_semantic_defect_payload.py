from __future__ import annotations

import pytest

from backend.analytics import read_models


def test_semantic_defect_payload_returns_rows_from_stable_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    query = object()
    snapshot_metadata = {"active_snapshot_version": "snapshot-1"}
    ticket_rows = [{"ticket_id": "D-1"}]

    monkeypatch.setattr(read_models, "normalize_query", lambda **_kwargs: query)
    monkeypatch.setattr(read_models, "_read_snapshot_metadata", lambda: snapshot_metadata)
    monkeypatch.setattr(read_models, "_resolve_effective_snapshot_version", lambda _metadata: "snapshot-1")
    monkeypatch.setattr(read_models, "_build_generated_from_payload", lambda _query: {"fetched_at": "2026-09-10T00:00:00Z"})
    monkeypatch.setattr(read_models, "has_materialized_dashboard_snapshot_rows", lambda _version: True)
    monkeypatch.setattr(read_models, "_load_materialized_ticket_rows", lambda **_kwargs: ticket_rows)

    result = read_models.build_semantic_defect_payload(years=["2026"])

    assert result == {
        "snapshot_version": "snapshot-1",
        "generated_from": {"fetched_at": "2026-09-10T00:00:00Z"},
        "refresh_metadata": snapshot_metadata,
        "ticket_rows": ticket_rows,
    }


def test_semantic_defect_payload_rejects_a_stale_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    query = read_models.FullPictureDashboardQuery()
    monkeypatch.setattr(read_models, "normalize_query", lambda **_kwargs: query)
    monkeypatch.setattr(read_models, "_read_snapshot_metadata", lambda: {"active_snapshot_version": "snapshot-current"})
    monkeypatch.setattr(read_models, "_resolve_effective_snapshot_version", lambda _metadata: "snapshot-current")

    with pytest.raises(read_models.FullPictureDashboardRequestError, match="Requested snapshot version is stale"):
        read_models.build_semantic_defect_payload(snapshot_version="snapshot-stale")


def test_semantic_defect_payload_materializes_a_live_snapshot_before_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(read_models, "normalize_query", lambda **_kwargs: read_models.FullPictureDashboardQuery())
    monkeypatch.setattr(read_models, "_read_snapshot_metadata", lambda: {})
    monkeypatch.setattr(read_models, "_resolve_effective_snapshot_version", lambda _metadata: "live-current")
    monkeypatch.setattr(read_models, "_build_generated_from_payload", lambda _query: {})
    monkeypatch.setattr(read_models, "_materialize_snapshot_ticket_rows", lambda _version: calls.append("materialize"))
    monkeypatch.setattr(read_models, "has_materialized_dashboard_snapshot_rows", lambda _version: True)

    def load_rows(**_kwargs: object) -> list[dict[str, object]]:
        calls.append("read")
        return [{"ticket_id": "D-1"}]

    monkeypatch.setattr(read_models, "_load_materialized_ticket_rows", load_rows)
    result = read_models.build_semantic_defect_payload()
    assert calls == ["materialize", "read"]
    assert result["ticket_rows"] == [{"ticket_id": "D-1"}]


def test_semantic_defect_payload_rejects_a_snapshot_changed_during_read(monkeypatch: pytest.MonkeyPatch) -> None:
    revisions = iter(["snapshot-before", "snapshot-after"])
    monkeypatch.setattr(read_models, "normalize_query", lambda **_kwargs: read_models.FullPictureDashboardQuery())
    monkeypatch.setattr(read_models, "_read_snapshot_metadata", lambda: {})
    monkeypatch.setattr(read_models, "_resolve_effective_snapshot_version", lambda _metadata: next(revisions))
    monkeypatch.setattr(read_models, "_build_generated_from_payload", lambda _query: {})
    monkeypatch.setattr(read_models, "_load_materialized_ticket_rows", lambda **_kwargs: [])
    monkeypatch.setattr(read_models, "has_materialized_dashboard_snapshot_rows", lambda _version: True)
    with pytest.raises(read_models.FullPictureDashboardRequestError, match="changed during request"):
        read_models.build_semantic_defect_payload()


def test_semantic_defect_payload_does_not_invent_a_year_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(read_models, "_read_snapshot_metadata", lambda: {})
    monkeypatch.setattr(read_models, "_resolve_effective_snapshot_version", lambda _metadata: "snapshot-all-years")
    monkeypatch.setattr(read_models, "has_materialized_dashboard_snapshot_rows", lambda _version: True)
    monkeypatch.setattr(read_models, "_build_generated_from_payload", lambda _query: {})
    rows = [{"ticket_id": "D-2025", "year": "2025"}, {"ticket_id": "D-2026", "year": "2026"}]
    monkeypatch.setattr(read_models, "_load_materialized_ticket_rows", lambda *, snapshot_version, query: [
        row for row in rows if not query.years or row["year"] in query.years
    ])
    assert read_models.build_semantic_defect_payload()["ticket_rows"] == rows
    assert read_models.build_semantic_defect_payload(years=["2026"])["ticket_rows"] == [rows[1]]


def test_semantic_defect_payload_rejects_unpublished_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(read_models, "_read_snapshot_metadata", lambda: {})
    monkeypatch.setattr(read_models, "_resolve_effective_snapshot_version", lambda _metadata: "snapshot-unpublished")
    monkeypatch.setattr(read_models, "has_materialized_dashboard_snapshot_rows", lambda _version: False)
    monkeypatch.setattr(read_models, "_build_generated_from_payload", lambda _query: {})
    monkeypatch.setattr(read_models, "_load_materialized_ticket_rows", lambda **_kwargs: [])
    with pytest.raises(read_models.FullPictureDashboardDataError, match="not available"):
        read_models.build_semantic_defect_payload()