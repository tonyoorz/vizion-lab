import sqlite3
import json

from openpyxl import load_workbook

from backend.analytics.open_ticket_postponement import (
    build_report_row,
    build_llm_input_record,
    build_ticket_record,
    classify_ticket,
    load_open_ticket_records,
    load_validated_llm_summaries,
    shift_confidence_category,
    write_open_shift_ticket_report,
    write_open_ticket_postponement_report,
)


def test_classify_ticket_requires_a_strictly_later_confirmed_target_release() -> None:
    postponed = classify_ticket(
        software_version="idcevo-pu2707-27w01.1-1",
        target_i_step="NA05-27-11-460",
    )
    same_release = classify_ticket(
        software_version="idcevo-pu2707-27w01.1-1",
        target_i_step="NA05-27-07-460",
    )

    assert postponed["classification"] == "confirmed_postponed"
    assert postponed["postponed_release_steps"] == 1
    assert same_release["classification"] == "same_target_not_postponed"


def test_build_ticket_record_keeps_target_solution_and_postponement_evidence() -> None:
    record = build_ticket_record(
        {
            "ticket_id": "D-100",
            "name": "Navigation screen stays blank",
            "phase": "04-In Progress",
            "software_version": "idcevo-pu2707-27w01.1-1",
            "description": "<p>Screen remains blank after restart.</p>",
            "comments": "[]",
        },
        [
            {
                "field_name": "target_i_step_udf",
                "old_value_text": "NA05-27-07-460",
                "new_value_text": "NA05-27-11-460",
                "event_timestamp": "2026-10-10T08:00:00Z",
            },
            {
                "field_name": "target_week_udf",
                "new_value_text": "CW47",
                "event_timestamp": "2026-10-10T08:01:00Z",
            },
            {
                "field_name": "postpone_toleratedate_udf",
                "new_value": "2026-10-09",
                "event_timestamp": "2026-10-10T08:02:00Z",
            },
            {
                "field_name": "postpone_tolerate_doc_udf",
                "new_value": "<p>Delivery moved because verification remains open.</p>",
                "event_timestamp": "2026-10-10T08:03:00Z",
            },
        ],
    )

    assert record["classification"] == "confirmed_postponed"
    assert record["target_i_step"] == "NA05-27-11-460"
    assert record["target_week"] == "CW47"
    assert record["postponement_decision_date"] == "2026-10-09"
    assert "NA05-27-07-460 -> NA05-27-11-460" in record["target_change_trail"]

    llm_input = build_llm_input_record(record)

    assert llm_input["ticket"]["target_i_step"] == "NA05-27-11-460"
    assert llm_input["ticket"]["postponement_document_evidence"] == (
        "Delivery moved because verification remains open."
    )
    assert "<p>" not in llm_input["ticket"]["problem_evidence"]
    assert llm_input["input_fingerprint"]


def test_build_ticket_record_excludes_non_later_requested_target_from_comparison_sheets() -> None:
    record = build_ticket_record(
        {
            "ticket_id": "D-REQUEST",
            "software_version": "idcevo-pu2707-27w01.1-1",
        },
        [
            {
                "field_name": "req_target_istep_udf",
                "new_value_text": "NA05-27-07-460",
                "event_timestamp": "2026-10-10T08:00:00Z",
            }
        ],
    )

    assert record["classification"] == "no_concrete_target"


def test_build_ticket_record_classifies_later_shift_to_set_without_target_i_step() -> None:
    record = build_ticket_record(
        {
            "ticket_id": "D-SHIFT",
            "phase": "04-In Progress",
            "software_version": "idcevo-pu2607-26w15.1-1",
        },
        [
            {
                "field_name": "target_set_udf",
                "new_value_text": "27-07",
                "event_timestamp": "2026-08-01T00:00:00Z",
            },
            {
                "field_name": "first_use_sop_of_function_udf",
                "new_value_text": "27-07",
                "event_timestamp": "2026-08-01T00:00:01Z",
            },
        ],
    )

    assert record["classification"] == "confirmed_shift_to_set"
    assert record["shift_to_set_pu"] == "27-07"
    assert record["postponed_release_steps"] == 3


def test_build_ticket_record_classifies_phase07_concrete_closed_version_as_future_fix_plan() -> None:
    record = build_ticket_record(
        {
            "ticket_id": "D-PHASE07",
            "phase": "07-In Pre-Verification",
            "software_version": ".",
        },
        [
            {
                "field_name": "closed_in_ver_udf",
                "new_value_text": "china-speech-app-2707.2.26120800",
                "event_timestamp": "2026-10-15T06:01:41Z",
            },
            {
                "field_name": "target_i_step_udf",
                "new_value_text": "No I-Step Relevance",
                "event_timestamp": "2026-10-15T06:01:19Z",
            },
        ],
    )

    assert record["classification"] == "phase07_concrete_fix_version_plan"
    assert record["phase07_fix_version_quality"] == "Concrete future fix version recorded"
    assert "china-speech-app-2707.2.26120800" in record["phase07_fix_versions"]


def test_build_ticket_record_does_not_treat_generic_phase07_closure_labels_as_versions() -> None:
    record = build_ticket_record(
        {
            "ticket_id": "D-PHASE07-GENERIC",
            "phase": "07-In Pre-Verification",
            "software_version": ".",
        },
        [
            {
                "field_name": "closed_in_ver_udf",
                "new_value_text": "Backendfix",
                "event_timestamp": "2026-10-15T06:01:41Z",
            },
            {
                "field_name": "sw_hrl_closed_version_udf",
                "new_value_text": "Application Modules",
                "event_timestamp": "2026-10-15T06:01:42Z",
            },
        ],
    )

    assert record["classification"] == "no_concrete_target"
    assert record["phase07_fix_version_quality"] == (
        "Generic closure label only; no concrete software version"
    )


def test_build_ticket_record_classifies_later_first_use_sop_as_future_plan_not_confirmed_fix() -> None:
    record = build_ticket_record(
        {
            "ticket_id": "D-SOP",
            "phase": "04-In Progress",
            "software_version": "idcevo-pu2607-26w15.1-1",
        },
        [
            {
                "field_name": "first_use_sop_of_function_udf",
                "new_value_text": "27-07",
                "event_timestamp": "2026-08-01T00:00:00Z",
            }
        ],
    )

    assert record["classification"] == "first_use_sop_future_plan"
    assert record["first_use_sop_pu"] == "27-07"
    assert record["postponed_release_steps"] == 3


def test_build_ticket_record_prioritizes_requested_target_over_first_use_sop() -> None:
    record = build_ticket_record(
        {
            "ticket_id": "D-REQUEST-PRIORITY",
            "phase": "04-In Progress",
            "software_version": "idcevo-pu2607-26w15.1-1",
        },
        [
            {
                "field_name": "req_target_istep_udf",
                "new_value_text": "NA05-27-07-460",
                "event_timestamp": "2026-08-01T00:00:00Z",
            },
            {
                "field_name": "first_use_sop_of_function_udf",
                "new_value_text": "27-11",
                "event_timestamp": "2026-08-01T00:00:01Z",
            },
        ],
    )

    assert record["classification"] == "requested_target_review"
    assert record["target_i_step"] == "NA05-27-07-460"


def test_build_ticket_record_preserves_multi_source_future_plan_signals() -> None:
    record = build_ticket_record(
        {
            "ticket_id": "D-PLAN-SIGNALS",
            "phase": "07-In Pre-Verification",
            "software_version": "idcevo-pu2607-26w15.1-1",
        },
        [
            {
                "field_name": "target_set_udf",
                "new_value_text": "27-07",
                "event_timestamp": "2026-08-01T00:00:00Z",
            },
            {
                "field_name": "first_use_sop_of_function_udf",
                "new_value_text": "27-11",
                "event_timestamp": "2026-08-01T00:00:01Z",
            },
            {
                "field_name": "closed_in_ver_udf",
                "new_value_text": "nightly/idc-mainline/27w03.1-1",
                "event_timestamp": "2026-08-01T00:00:02Z",
            },
            {
                "field_name": "predicted_target_i_step_udf",
                "new_value_text": "NA05-27-11-460",
                "event_timestamp": "2026-08-01T00:00:03Z",
            },
            {
                "field_name": "supplier_confirmed_i_step_udf",
                "new_value_text": "NA05-27-07-460",
                "event_timestamp": "2026-08-01T00:00:04Z",
            },
        ],
    )

    assert record["has_later_shift_to_set"] is True
    assert record["has_later_first_use_sop"] is True
    assert record["has_concrete_phase07_fix_version"] is True
    assert record["predicted_target_i_step"] == "NA05-27-11-460"
    assert record["supplier_confirmed_i_step"] == "NA05-27-07-460"
    assert "Shift-to-Set PU: 27-07" in record["plan_evidence_signals"]
    assert "Closed in version: nightly/idc-mainline/27w03.1-1" in record[
        "plan_evidence_signals"
    ]


def test_build_report_row_exposes_target_solution_and_llm_assessment() -> None:
    row = build_report_row(
        {
            "ticket_id": "D-100",
            "name": "Navigation screen stays blank",
            "phase": "04-In Progress",
            "software_version": "idcevo-pu2707-27w01.1-1",
            "reported_release": "27-07",
            "target_i_step": "NA05-27-11-460",
            "target_release": "27-11",
            "target_evidence_field": "target_i_step_udf",
            "target_recorded_at": "2026-10-10T08:00:00Z",
            "target_week": "CW47",
            "postponement_decision_date": "2026-10-09",
            "target_change_trail": "27-07 -> 27-11",
            "classification": "confirmed_postponed",
            "postponed_release_steps": 1,
        },
        {
            "source": "llm",
            "model": "fixture-model",
            "issue_summary": "重启后导航屏幕仍为空白。",
            "postponement_assessment": "目标版本由 27-07 调整为 27-11。",
            "review_focus": "核实验证计划。",
        },
    )

    assert row["Source Software (full)"] == "idcevo-pu2707-27w01.1-1"
    assert row["Target Software / I-Step (full)"] == "NA05-27-11-460"
    assert row["Target PU/I-Step"] == "27-11"
    assert row["LLM Postponement Assessment"] == "目标版本由 27-07 调整为 27-11。"


def test_load_open_ticket_records_uses_latest_target_history_for_open_tickets(tmp_path) -> None:
    db_path = tmp_path / "tickets.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(
            """
            CREATE TABLE octane_defects (
                defect_id TEXT,
                name TEXT,
                phase TEXT,
                project TEXT,
                tproject TEXT,
                team TEXT,
                problem_finder_team TEXT,
                function_responsible TEXT,
                solution_responsible TEXT,
                creation_time TEXT,
                last_modified TEXT,
                software_version TEXT,
                description TEXT,
                comments TEXT
            );
            CREATE TABLE octane_defect_history_events (
                defect_id TEXT,
                field_name TEXT,
                old_value TEXT,
                new_value TEXT,
                old_value_text TEXT,
                new_value_text TEXT,
                event_timestamp TEXT,
                entry_index INTEGER,
                change_index INTEGER
            );
            """
        )
        connection.executemany(
            "INSERT INTO octane_defects VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "D-OPEN",
                    "Open ticket",
                    "04-In Progress",
                    "IDCEVO",
                    "IDCEVO",
                    "FIT",
                    "FIT",
                    "Owner",
                    "bmw",
                    "2026-01-01T00:00:00Z",
                    "2026-10-10T00:00:00Z",
                    "idcevo-pu2707-27w01.1-1",
                    "Description",
                    "[]",
                ),
                (
                    "D-CLOSED",
                    "Closed ticket",
                    "06-Concluded",
                    "IDCEVO",
                    "IDCEVO",
                    "FIT",
                    "FIT",
                    "Owner",
                    "bmw",
                    "2026-01-01T00:00:00Z",
                    "2026-10-10T00:00:00Z",
                    "idcevo-pu2707-27w01.1-1",
                    "Description",
                    "[]",
                ),
            ],
        )
        connection.executemany(
            "INSERT INTO octane_defect_history_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "D-OPEN",
                    "target_i_step_udf",
                    "",
                    "old-id",
                    "",
                    "NA05-27-07-460",
                    "2026-10-01T00:00:00Z",
                    1,
                    1,
                ),
                (
                    "D-OPEN",
                    "target_i_step_udf",
                    "old-id",
                    "new-id",
                    "NA05-27-07-460",
                    "NA05-27-11-460",
                    "2026-10-10T00:00:00Z",
                    2,
                    1,
                ),
            ],
        )
        connection.commit()
    finally:
        connection.close()

    records = load_open_ticket_records(db_path)

    assert [record["ticket_id"] for record in records] == ["D-OPEN"]
    assert records[0]["target_i_step"] == "NA05-27-11-460"
    assert records[0]["classification"] == "confirmed_postponed"


def test_write_report_creates_enriched_classification_sheets(tmp_path) -> None:
    record = build_ticket_record(
        {
            "ticket_id": "D-100",
            "name": "Navigation screen stays blank",
            "phase": "04-In Progress",
            "software_version": "idcevo-pu2707-27w01.1-1",
            "description": "Screen remains blank after restart.",
        },
        [
            {
                "field_name": "target_i_step_udf",
                "new_value_text": "NA05-27-11-460",
                "event_timestamp": "2026-10-10T08:00:00Z",
            }
        ],
    )
    output_path = tmp_path / "postponement.xlsx"

    write_open_ticket_postponement_report(
        [record],
        output_path=output_path,
        llm_summaries={
            "D-100": {
                "source": "llm",
                "model": "fixture-model",
                "issue_summary": "重启后导航屏幕仍为空白。",
                "postponement_assessment": "目标版本调整到 27-11。",
                "review_focus": "核实验证计划。",
            }
        },
        source_snapshot="2026-10-15T07:59:30Z",
    )

    workbook = load_workbook(output_path, data_only=True)
    assert workbook.sheetnames == [
        "00_Definitions",
        "01_Confirmed_Postponed",
        "02_Requested_Target_Review",
        "03_Same_Target_Not_Postponed",
        "04_Earlier_Target_Data_Check",
        "05_Manual_Version_Comparison",
    ]
    worksheet = workbook["01_Confirmed_Postponed"]
    headers = [cell.value for cell in next(worksheet.iter_rows(max_row=1))]
    values = next(worksheet.iter_rows(min_row=2, max_row=2, values_only=True))
    row = dict(zip(headers, values, strict=True))
    assert row["Target Software / I-Step (full)"] == "NA05-27-11-460"
    assert row["LLM Issue Summary"] == "重启后导航屏幕仍为空白。"


def test_shift_confidence_category_prioritizes_confirmed_pu_shift() -> None:
    record = build_ticket_record(
        {
            "ticket_id": "D-SHIFT-CONFIDENCE",
            "phase": "07-In Pre-Verification",
            "software_version": "idcevo-pu2607-26w15.1-1",
        },
        [
            {
                "field_name": "target_set_udf",
                "new_value_text": "27-07",
                "event_timestamp": "2026-08-01T00:00:00Z",
            },
            {
                "field_name": "closed_in_ver_udf",
                "new_value_text": "nightly/idc-mainline/27w03.1-1",
                "event_timestamp": "2026-08-01T00:00:01Z",
            },
        ],
    )

    category, basis = shift_confidence_category(record)

    assert category == "Confirmed PU Shift"
    assert "Shift-to-Set" in basis


def test_write_open_shift_ticket_report_creates_single_filterable_data_tab(tmp_path) -> None:
    record = build_ticket_record(
        {
            "ticket_id": "D-SHIFT-REPORT",
            "phase": "07-In Pre-Verification",
            "software_version": "idcevo-pu2607-26w15.1-1",
        },
        [
            {
                "field_name": "target_set_udf",
                "new_value_text": "27-07",
                "event_timestamp": "2026-08-01T00:00:00Z",
            },
            {
                "field_name": "first_use_sop_of_function_udf",
                "new_value_text": "27-11",
                "event_timestamp": "2026-08-01T00:00:01Z",
            },
            {
                "field_name": "closed_in_ver_udf",
                "new_value_text": "nightly/idc-mainline/27w03.1-1",
                "event_timestamp": "2026-08-01T00:00:02Z",
            },
        ],
    )
    output_path = tmp_path / "open_shift_tickets.xlsx"

    write_open_shift_ticket_report(
        [record],
        output_path=output_path,
        llm_summaries={
            "D-SHIFT-REPORT": {
                "source": "llm",
                "model": "fixture-model",
                "issue_summary": "问题摘要。",
                "postponement_assessment": "计划摘要。",
                "review_focus": "核实摘要。",
            }
        },
        source_snapshot="2026-10-15T07:59:30Z",
    )

    workbook = load_workbook(output_path, data_only=True)
    assert workbook.sheetnames == ["Open_Shift_Tickets"]
    worksheet = workbook["Open_Shift_Tickets"]
    header_row = next(
        row_number
        for row_number in range(1, 30)
        if worksheet.cell(row_number, 1).value == "Confidence Category"
    )
    headers = [cell.value for cell in next(worksheet.iter_rows(min_row=header_row, max_row=header_row))]
    values = next(
        worksheet.iter_rows(
            min_row=header_row + 1,
            max_row=header_row + 1,
            values_only=True,
        )
    )
    row = dict(zip(headers, values, strict=True))
    assert row["Shift-to-Set PU"] == "27-07"
    assert row["Phase 07 Fix Version Quality"] == "Concrete future fix version recorded"
    assert row["Confidence Category"] == "Confirmed PU Shift"
    assert worksheet.freeze_panes is None
    assert worksheet.row_dimensions[header_row + 1].height == 42


def test_load_validated_llm_summaries_rejects_stale_fingerprints(tmp_path) -> None:
    input_record = build_llm_input_record(
        build_ticket_record(
            {
                "ticket_id": "D-100",
                "software_version": "idcevo-pu2707-27w01.1-1",
            },
            [
                {
                    "field_name": "target_i_step_udf",
                    "new_value_text": "NA05-27-11-460",
                    "event_timestamp": "2026-10-10T08:00:00Z",
                }
            ],
        )
    )
    summaries_path = tmp_path / "summaries.jsonl"
    summaries_path.write_text(
        "\n".join(
            (
                json.dumps(
                    {
                        "ticket_id": "D-100",
                        "input_fingerprint": input_record["input_fingerprint"],
                        "source": "llm",
                        "model": "fixture-model",
                        "issue_summary": "问题。",
                        "postponement_assessment": "延期。",
                        "postponement_reason_analysis": "原因未记录。",
                        "resolution_timing_analysis": "时间未记录。",
                        "resolution_version_analysis": "版本未记录。",
                        "review_focus": "核实。",
                    }
                ),
                json.dumps(
                    {
                        "ticket_id": "D-STALE",
                        "input_fingerprint": "stale",
                        "source": "llm",
                        "issue_summary": "stale",
                    }
                ),
            )
        )
        + "\n",
        encoding="utf-8",
    )

    summaries = load_validated_llm_summaries([input_record], summaries_path)

    assert input_record["input_version"] == "open-shift-ticket-plan-llm-v3"
    assert list(summaries) == ["D-100"]
    assert summaries["D-100"]["model"] == "fixture-model"
