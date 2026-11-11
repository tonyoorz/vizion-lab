from openpyxl import load_workbook

from backend.analytics.phase09_wide_postponement_scan import (
    classify_shift_tolerate_process,
    classify_wide_postponement_evidence,
    write_shift_tolerate_process_excel,
    write_wide_postponement_scan_excel,
)


def test_classify_wide_postponement_evidence_uses_unique_strength_order() -> None:
    confirmed = classify_wide_postponement_evidence(
        source_software_version="idcevo-pu2607-26w15.1-1",
        history_events=[
            {
                "field_name": "target_i_step_udf",
                "new_value_text": "NA05-27-07-460",
                "event_timestamp": "2026-10-10T00:00:00Z",
            }
        ],
        description="Issue remains reproducible.",
        comments=[],
    )
    explicit_text = classify_wide_postponement_evidence(
        source_software_version="checkin",
        history_events=[],
        description="Fix is planned for release 27-07 after supplier validation.",
        comments=[],
    )
    first_use = classify_wide_postponement_evidence(
        source_software_version="checkin",
        history_events=[
            {
                "field_name": "first_use_sop_of_function_udf",
                "new_value_text": "27-07",
                "event_timestamp": "2026-10-10T00:00:00Z",
            }
        ],
        description="Issue remains reproducible.",
        comments=[],
    )
    weak = classify_wide_postponement_evidence(
        source_software_version="checkin",
        history_events=[],
        description="The issue is tolerated pending further review.",
        comments=[],
    )

    assert confirmed["evidence_tier"] == "Confirmed delayed"
    assert confirmed["target_release"] == "27-07"
    assert explicit_text["evidence_tier"] == "Explicit text plan"
    assert first_use["evidence_tier"] == "First Use/SOP context"
    assert weak["evidence_tier"] == "Weak signal - review"


def test_write_wide_postponement_scan_excel_keeps_unique_tiers_and_evidence(tmp_path) -> None:
    output_path = tmp_path / "wide_scan.xlsx"
    records = [
        {
            "ticket_id": "D-CONFIRMED",
            "cohort": "Phase 09 FullLLM",
            "current_phase": "09-Concluded without action",
            "title": "Navigation guidance disappears",
            "source_software_version": "idcevo-pu2607-26w15.1-1",
            "source_release": "26-07",
            "target_i_step": "NA05-27-07-460",
            "target_release": "27-07",
            "evidence_tier": "Confirmed delayed",
            "evidence_basis": "Target I-Step is later.",
            "evidence_matches": [
                {"source": "History", "field_name": "target_i_step_udf", "excerpt": "Target I-Step: NA05-27-07-460"}
            ],
        },
        {
            "ticket_id": "D-WEAK",
            "cohort": "Current open",
            "current_phase": "04-In Progress",
            "title": "Speech issue",
            "source_software_version": "checkin",
            "source_release": "",
            "target_i_step": "",
            "target_release": "",
            "evidence_tier": "Weak signal - review",
            "evidence_basis": "Text mentions postpone.",
            "evidence_matches": [
                {"source": "Comment", "field_name": "", "excerpt": "Issue is tolerated."}
            ],
        },
    ]

    write_wide_postponement_scan_excel(
        records,
        output_path=output_path,
        source_snapshot="2026-10-15T07:59:30Z",
    )

    workbook = load_workbook(output_path, read_only=True, data_only=True)
    assert workbook.sheetnames == ["00_总览", "01_宽口径候选", "02_强证据", "03_文本计划", "04_弱信号复核", "08_命中证据"]
    assert workbook.worksheets[1].max_row - 1 == 2
    assert workbook.worksheets[5].max_row - 1 == 2


def test_classify_shift_tolerate_process_requires_formal_or_explicit_process_evidence() -> None:
    target_only = classify_shift_tolerate_process(
        history_events=[
            {
                "field_name": "target_i_step_udf",
                "new_value_text": "NA05-27-07-460",
            }
        ],
        description="Issue remains reproducible.",
        comments=[],
    )
    formal = classify_shift_tolerate_process(
        history_events=[
            {
                "field_name": "postpone_toleratedate_udf",
                "new_value": "2026-10-10",
            }
        ],
        description="Issue remains reproducible.",
        comments=[],
    )
    explicit = classify_shift_tolerate_process(
        history_events=[],
        description="Shift to 27-07 is agreed; fix is planned in that release.",
        comments=[],
    )

    assert target_only["process_tier"] == "No process evidence"
    assert formal["process_tier"] == "Formal Shift/Tolerate process"
    assert explicit["process_tier"] == "Explicit process + version"
    assert explicit["current_state_assessed"] is False
    assert explicit["process_evidence_scope"] == "historical_candidate"
    assert formal["human_review_required"] is True
    assert "Lexical" in explicit["process_basis"]


def test_write_shift_tolerate_process_excel_excludes_non_process_candidates(tmp_path) -> None:
    output_path = tmp_path / "process_only.xlsx"
    records = [
        {
            "ticket_id": "D-FORMAL",
            "cohort": "Phase 09 FullLLM",
            "current_phase": "09-Concluded without action",
            "title": "Navigation issue",
            "source_software_version": "idcevo-pu2607-26w15.1-1",
            "target_i_step": "NA05-27-07-460",
            "process_tier": "Formal Shift/Tolerate process",
            "process_basis": "Recorded decision date.",
            "process_matches": [
                {"source": "History", "field_name": "postpone_toleratedate_udf", "excerpt": "decision date: 2026-10-10"}
            ],
        },
        {
            "ticket_id": "D-TEXT",
            "cohort": "Current open",
            "current_phase": "04-In Progress",
            "title": "Speech issue",
            "source_software_version": "checkin",
            "target_i_step": "",
            "process_tier": "Explicit process + version",
            "process_basis": "Shift to 27-07 recorded.",
            "process_matches": [
                {"source": "Comment", "field_name": "", "excerpt": "Shift to 27-07 is agreed."}
            ],
        },
    ]

    write_shift_tolerate_process_excel(
        records,
        output_path=output_path,
        source_snapshot="2026-10-15T07:59:30Z",
    )

    workbook = load_workbook(output_path, read_only=True, data_only=True)
    assert workbook.sheetnames == ["00_总览", "01_流程候选", "02_正式流程", "03_文本流程", "08_流程证据"]
    assert workbook.worksheets[1].max_row - 1 == 2
    assert workbook.worksheets[4].max_row - 1 == 2