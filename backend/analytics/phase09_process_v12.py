from __future__ import annotations

from typing import Mapping, Sequence

from backend.analytics.phase09_idcevo_scenario_build import classify_service_function


FUTURE_RESOLUTION_STATUS = "Recorded Shift/Tolerate process plan - not verified repair"


def _text(value: object) -> str:
    return str(value or "").strip()


def _future_resolution_plan(record: Mapping[str, object]) -> str:
    lines = [
        "Process tier: {}".format(_text(record.get("process_tier"))),
        "Process basis: {}".format(_text(record.get("process_basis"))),
    ]
    matches = record.get("process_matches")
    if isinstance(matches, list):
        for match in matches:
            if not isinstance(match, Mapping):
                continue
            lines.append(
                "{} {}: {}".format(
                    _text(match.get("source")) or "Evidence",
                    _text(match.get("field_name")),
                    _text(match.get("excerpt")),
                ).strip()
            )
    return "\n".join(line for line in lines if line.strip())


def _lifecycle_status(record: Mapping[str, object]) -> str:
    explicit_status = _text(record.get("ticket_lifecycle_status"))
    if explicit_status:
        return explicit_status
    phase = _text(record.get("phase") or record.get("current_phase"))
    cohort = _text(record.get("cohort"))
    if "phase 09" in cohort.casefold() or phase[:2] in {"06", "09"}:
        return "Closed / Phase 09"
    return "Open / active"


def _joined_unique(values: Sequence[object], *, separator: str = "\n---\n") -> str:
    unique_values: list[str] = []
    for value in values:
        text = _text(value)
        if text and text not in unique_values:
            unique_values.append(text)
    return separator.join(unique_values)


def _selection_scope(record: Mapping[str, object], default: str) -> str:
    return _text(record.get("selection_scope")) or default


def merge_shift_tolerate_records(
    process_records: Sequence[Mapping[str, object]],
    open_shift_records: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Merge distinct Shift/Tolerate cohorts while retaining their evidence scope."""
    records_by_ticket_id: dict[str, list[tuple[str, Mapping[str, object]]]] = {}
    for source, records in (
        ("Strict Shift/Tolerate process", process_records),
        ("Open Shift", open_shift_records),
    ):
        for record in records:
            ticket_id = _text(record.get("ticket_id"))
            if ticket_id:
                records_by_ticket_id.setdefault(ticket_id, []).append((source, record))

    merged_records: list[dict[str, object]] = []
    for ticket_id, source_records in records_by_ticket_id.items():
        process_record = next(
            (
                record
                for source, record in source_records
                if source == "Strict Shift/Tolerate process"
            ),
            None,
        )
        primary_record = process_record or source_records[0][1]
        merged = dict(primary_record)
        scopes = [
            _selection_scope(record, source)
            for source, record in source_records
        ]
        scopes.sort(
            key=lambda value: (
                0 if value.casefold().startswith("open shift") else 1,
                value,
            )
        )
        merged["ticket_id"] = ticket_id
        merged["phase"] = _text(
            primary_record.get("phase") or primary_record.get("current_phase")
        )
        merged["ticket_lifecycle_status"] = _lifecycle_status(primary_record)
        merged["selection_scope"] = _joined_unique(scopes, separator="; ")
        for field_name in (
            "future_resolution_plan",
            "plan_evidence_signals",
            "postponement_reason_analysis",
            "resolution_timing_analysis",
            "resolution_version_analysis",
        ):
            merged[field_name] = _joined_unique(
                [record.get(field_name) for _source, record in source_records]
            )
        for field_name in (
            "title",
            "issue_summary",
            "source_software_version",
            "source_release",
            "target_i_step",
            "target_release",
            "shift_to_set_pu",
            "first_use_sop_pu",
            "closed_in_version",
            "sw_hrl_closed_version",
            "app_version",
            "description",
            "comments",
            "rca_root_cause",
            "rca_recovery",
            "rca_observed",
            "rca_expected",
            "rca_evidence_source",
        ):
            if _text(merged.get(field_name)):
                continue
            for _source, record in source_records:
                value = _text(record.get(field_name))
                if value:
                    merged[field_name] = value
                    break
        merged_records.append(merged)
    return sorted(merged_records, key=lambda record: _text(record.get("ticket_id")))


def build_process_full_records(
    records: Sequence[Mapping[str, object]],
    summaries_by_ticket_id: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]:
    full_records: list[dict[str, object]] = []
    for record in records:
        ticket_id = _text(record.get("ticket_id"))
        summary = summaries_by_ticket_id.get(ticket_id)
        if not summary:
            continue
        raw_function = (
            _text(record.get("solution_cluster"))
            or _text(record.get("product_areas"))
            or _text(record.get("defect_category"))
        )
        issue_summary = _text(summary.get("issue_summary")) or _text(record.get("title"))
        service = classify_service_function(
            {
                "rep_function": raw_function,
                "function": raw_function,
                "function_responsible": _text(record.get("function_responsible")),
                "affected_function": raw_function,
                "rep_title": _text(record.get("title")),
                "customer_symptom": issue_summary,
                "trigger_condition": "",
            }
        )
        full_records.append(
            {
                "ticket_id": ticket_id,
                "title": _text(record.get("title")),
                "phase": _text(record.get("current_phase")),
                "ticket_lifecycle_status": _lifecycle_status(record),
                "selection_scope": "Strict Shift/Tolerate process",
                "service_function_id": _text(service.get("service_function_id")),
                "service_function": _text(service.get("service_function")),
                "service_mapping_source": _text(service.get("match_source")),
                "service_domain": _text(service.get("service_domain")),
                "confidence_category": _text(record.get("process_tier")),
                "confidence_basis": _text(record.get("process_basis")),
                "issue_summary": issue_summary,
                "postponement_assessment": _text(summary.get("postponement_assessment")),
                "postponement_reason_analysis": _text(summary.get("postponement_reason_analysis")),
                "resolution_timing_analysis": _text(summary.get("resolution_timing_analysis")),
                "resolution_version_analysis": _text(summary.get("resolution_version_analysis")),
                "review_focus": _text(summary.get("review_focus")),
                "future_resolution_status": FUTURE_RESOLUTION_STATUS,
                "future_resolution_plan": _future_resolution_plan(record),
                "source_software_version": _text(record.get("source_software_version")),
                "source_release": "",
                "target_i_step": _text(record.get("target_i_step")),
                "target_release": "",
                "shift_to_set_pu": "",
                "first_use_sop_pu": "",
                "closed_in_version": "",
                "sw_hrl_closed_version": "",
                "app_version": "",
                "function_responsible": _text(record.get("function_responsible")),
                "solution_cluster": _text(record.get("solution_cluster")),
                "product_areas": _text(record.get("product_areas")),
                "defect_category": _text(record.get("defect_category")),
                "assigned_ecu": _text(record.get("assigned_ecu")),
                "llm_source": "existing_validated_summary",
                "llm_model": _text(summary.get("llm_model") or summary.get("model")),
                "description": _text(record.get("description")),
                "comments": _text(record.get("comments")),
                "plan_evidence_signals": _future_resolution_plan(record),
                "rca_root_cause": _text(summary.get("rca_root_cause")),
                "rca_recovery": _text(summary.get("rca_recovery")),
                "rca_observed": _text(summary.get("rca_observed")),
                "rca_expected": _text(summary.get("rca_expected")),
                "rca_evidence_source": _text(summary.get("rca_evidence_source")),
                "process_tier": _text(record.get("process_tier")),
                "process_basis": _text(record.get("process_basis")),
                "process_matches": list(record.get("process_matches") or []),
            }
        )
    return sorted(full_records, key=lambda item: _text(item.get("ticket_id")))