from __future__ import annotations

import pytest

from backend.analytics import defect_query_models
from backend.analytics.read_models import FullPictureDashboardRequestError


ACTOR_SCOPE_HASH = "scope-alice"
DRILLDOWN_SECRET = "defect-records-pagination-test-secret"


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


def test_defect_records_continue_with_a_scope_bound_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    drilldown_ref = _signed_drilldown_ref(monkeypatch)

    first_page = defect_query_models.build_defect_records_payload(
        drilldown_ref=drilldown_ref,
        limit=2,
        agent_actor_scope_hash=ACTOR_SCOPE_HASH,
        agent_drilldown_secret=DRILLDOWN_SECRET,
    )

    assert first_page["page"] == 1
    assert first_page["page_size"] == 2
    assert [row["ticket_id"] for row in first_page["rows"]] == ["D-1", "D-2"]
    assert first_page["total_rows"] == 3
    assert first_page["truncated"] is True
    assert isinstance(first_page["next_cursor"], str)

    second_page = defect_query_models.build_defect_records_payload(
        drilldown_ref=drilldown_ref,
        cursor=first_page["next_cursor"],
        agent_actor_scope_hash=ACTOR_SCOPE_HASH,
        agent_drilldown_secret=DRILLDOWN_SECRET,
    )

    assert second_page["page"] == 2
    assert second_page["page_size"] == 2
    assert [row["ticket_id"] for row in second_page["rows"]] == ["D-3"]
    assert second_page["truncated"] is False
    assert second_page["next_cursor"] is None


def test_defect_records_reject_a_tampered_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    drilldown_ref = _signed_drilldown_ref(monkeypatch)
    first_page = defect_query_models.build_defect_records_payload(
        drilldown_ref=drilldown_ref,
        limit=2,
        agent_actor_scope_hash=ACTOR_SCOPE_HASH,
        agent_drilldown_secret=DRILLDOWN_SECRET,
    )
    cursor = str(first_page["next_cursor"])
    tampered_cursor = f"{cursor[:-1]}{'A' if cursor[-1] != 'A' else 'B'}"

    with pytest.raises(FullPictureDashboardRequestError, match="Invalid agent records cursor"):
        defect_query_models.build_defect_records_payload(
            drilldown_ref=drilldown_ref,
            cursor=tampered_cursor,
            agent_actor_scope_hash=ACTOR_SCOPE_HASH,
            agent_drilldown_secret=DRILLDOWN_SECRET,
        )