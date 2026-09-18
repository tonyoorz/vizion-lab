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
    monkeypatch.setattr(read_models, "_load_materialized_ticket_rows", lambda **_kwargs: ticket_rows)

    result = read_models.build_semantic_defect_payload(years=["2026"])

    assert result == {
        "snapshot_version": "snapshot-1",
        "generated_from": {"fetched_at": "2026-09-10T00:00:00Z"},
        "refresh_metadata": snapshot_metadata,
        "ticket_rows": ticket_rows,
    }


def test_semantic_defect_payload_rejects_a_stale_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    query = object()
    monkeypatch.setattr(read_models, "normalize_query", lambda **_kwargs: query)
    monkeypatch.setattr(read_models, "_read_snapshot_metadata", lambda: {"active_snapshot_version": "snapshot-current"})
    monkeypatch.setattr(read_models, "_resolve_effective_snapshot_version", lambda _metadata: "snapshot-current")

    with pytest.raises(read_models.FullPictureDashboardRequestError, match="Requested snapshot version is stale"):
        read_models.build_semantic_defect_payload(snapshot_version="snapshot-stale")