from __future__ import annotations

import csv
import io
import time

import pytest

from backend.analytics import defect_query_models


ACTOR_SCOPE_HASH = "scope-alice"
DRILLDOWN_SECRET = "defect-records-export-test-secret"


def _signed_drilldown_ref(monkeypatch: pytest.MonkeyPatch) -> str:
    ticket_rows = [
        {"ticket_id": "D-1", "ticket_name": "First"},
        {"ticket_id": "D-2", "ticket_name": "Second"},
        {"ticket_id": "D-3", "ticket_name": "Third"},
    ]
    monkeypatch.setattr(defect_query_models, "_current_snapshot_version", lambda: "snapshot-1")
    monkeypatch.setattr(defect_query_models, "_load_rows", lambda *_args, **_kwargs: ticket_rows)
    aggregate = defect_query_models.build_defect_aggregate_payload(
        metrics=["defect_count"],
        dimensions=[],
        derived_metrics=[],
        filters={},
        time={"field": "creation_time", "timezone": "Asia/Shanghai"},
        order_by=[{"field": "defect_count", "direction": "desc"}],
        limit=12,
        agent_actor_scope_hash=ACTOR_SCOPE_HASH,
        agent_drilldown_secret=DRILLDOWN_SECRET,
    )
    return aggregate["rows"][0]["drilldown_ref"]


def test_defect_records_export_streams_every_authorized_row(monkeypatch: pytest.MonkeyPatch) -> None:
    drilldown_ref = _signed_drilldown_ref(monkeypatch)
    started_at = int(time.time())

    export = defect_query_models.create_defect_records_export_payload(
        drilldown_ref=drilldown_ref,
        agent_actor_scope_hash=ACTOR_SCOPE_HASH,
        agent_drilldown_secret=DRILLDOWN_SECRET,
    )
    filename, csv_chunks = defect_query_models.stream_defect_records_export_csv(
        str(export["download_token"]),
        secret=DRILLDOWN_SECRET,
    )

    rows = list(csv.DictReader(io.StringIO("".join(csv_chunks))))

    assert filename.startswith("defect-records-")
    assert filename.endswith(".csv")
    assert started_at < int(export["expires_at"]) <= started_at + 601
    assert [row["ticket_id"] for row in rows] == ["D-1", "D-2", "D-3"]