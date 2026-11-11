from openpyxl import load_workbook

from backend.analytics.phase09_postponed_v4 import (
    build_phase09_postponed_v4_inputs,
    build_phase09_postponed_records,
    write_phase09_postponed_v4_excel,
)


def test_build_phase09_postponed_records_requires_target_later_than_reported_software() -> None:
    records = build_phase09_postponed_records(
        [
            {
                "ticket_id": "D-LATER",
                "source_software_version": "idcevo-pu2607-26w15.1-1",
                "target_i_step": "NA05-27-07-460",
                "target_recorded_at": "2026-10-10T00:00:00Z",
                "validation_release_ticket_count": 12,
            },
            {
                "ticket_id": "D-SAME",
                "source_software_version": "idcevo-pu2707-27w15.1-1",
                "target_i_step": "NA05-27-07-460",
                "target_recorded_at": "2026-10-10T00:00:00Z",
                "validation_release_ticket_count": 0,
            },
        ]
    )

    assert [record["ticket_id"] for record in records] == ["D-LATER"]
    assert records[0]["target_release"] == "27-07"
    assert records[0]["validation_evidence_status"].startswith("Release-level environment observed")


def test_write_phase09_postponed_v4_excel_includes_llm_reason_timing_and_version(tmp_path) -> None:
    output_path = tmp_path / "phase09_v4.xlsx"
    records = [
        {
            "ticket_id": "D-LATER",
            "title": "Navigation guidance disappears",
            "phase": "09-Concluded without action",
            "source_software_version": "idcevo-pu2607-26w15.1-1",
            "source_release": "26-07",
            "target_i_step": "NA05-27-07-460",
            "target_release": "27-07",
            "target_recorded_at": "2026-10-10T00:00:00Z",
            "validation_release_ticket_count": 12,
            "validation_evidence_status": "Release-level environment observed in 12 Phase 06/07/08 tickets; no ticket-specific validation link recorded.",
            "issue_summary": "导航引导消失。",
            "postponement_reason_analysis": "记录未说明具体延期原因。",
            "resolution_timing_analysis": "未记录目标周。",
            "resolution_version_analysis": "目标 I-Step 为 NA05-27-07-460。",
            "review_focus": "核实该 ticket 的验证证据。",
            "function_responsible": "Owner A",
            "solution_cluster": "Navigation CN",
        }
    ]

    write_phase09_postponed_v4_excel(
        records,
        raw_evidence=[],
        output_path=output_path,
        source_snapshot="2026-10-15T07:59:30Z",
        model_label="glm-5.3-flash",
    )

    workbook = load_workbook(output_path, read_only=True, data_only=True)
    assert workbook.sheetnames == ["00_总览", "01_延期候选", "02_验证证据", "06_全部明细", "08_证据原文"]
    headers = [
        cell.value
        for cell in next(workbook.worksheets[1].iter_rows(min_row=1, max_row=1))
    ]
    assert "LLM 延期原因分析" in headers
    assert "LLM 解决时间分析" in headers
    assert "LLM 解决版本分析" in headers


def test_build_phase09_postponed_v4_inputs_uses_dedicated_fingerprint_version() -> None:
    inputs = build_phase09_postponed_v4_inputs(
        [
            {
                "ticket_id": "D-LATER",
                "title": "Navigation guidance disappears",
                "phase": "09-Concluded without action",
                "description": "Original description",
                "comments": "Original comment",
                "history_evidence": "target_i_step_udf: 26-07 -> 27-07",
                "source_software_version": "idcevo-pu2607-26w15.1-1",
                "source_release": "26-07",
                "target_i_step": "NA05-27-07-460",
                "target_release": "27-07",
                "target_recorded_at": "2026-10-10T00:00:00Z",
                "target_week": "CW47",
                "validation_release_ticket_count": 12,
                "validation_evidence_status": "Release-level environment observed in 12 Phase 06/07/08 tickets; no ticket-specific validation link recorded.",
            }
        ]
    )

    assert len(inputs) == 1
    assert inputs[0]["input_version"] == "phase09-postponed-v4-glm53-flash"
    assert inputs[0]["ticket"]["validation_evidence_status"].startswith("Release-level")
    assert inputs[0]["input_fingerprint"]