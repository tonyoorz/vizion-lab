import json
from pathlib import Path
import re
import sqlite3

from openpyxl import load_workbook

from backend.analytics.phase09_idcevo_excel_report import (
    generate_phase09_idcevo_excel_report,
)
from backend.analytics.phase09_idcevo_report import generate_phase09_idcevo_report
from backend.analytics.schema import ensure_schema


def _report_payload(html: str) -> dict[str, object]:
    match = re.search(r"const REPORT_DATA = (.*?);\n", html)
    assert match is not None
    return json.loads(match.group(1))


def test_generate_phase09_idcevo_report_classifies_scope_and_risk_evidence(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "qgate_raw.db"
    output_path = tmp_path / "idcevo_phase09_risk.html"
    ensure_schema(db_path)

    conn = sqlite3.connect(db_path)
    try:
        existing_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(octane_defects)").fetchall()
        }
        for column_name in (
            "description",
            "comments",
            "creation_time",
            "year",
            "phase",
            "team",
            "problem_finder_team",
            "solution_cluster",
            "defect_category",
                "product_areas",
            "blocking_reason",
            "reprel_changes",
            "severity",
            "problem_severity",
            "reporting_class",
            "parent_child_type",
            "relation_to",
        ):
            if column_name not in existing_columns:
                conn.execute(f"ALTER TABLE octane_defects ADD COLUMN {column_name} TEXT")
        conn.commit()
    finally:
        conn.close()

    risk_description = "Observed failure remains reproducible. " + "x" * 33_000
    defects = [
        (
            "D-LOW",
            "Expected behavior is documented",
            "No independent customer impact evidence.",
            "[]",
            "2025-01-01T00:00:00Z",
            "2025",
            "IDCEVO",
            "09-Concluded without action",
            "Core UI",
            "CN Speech",
            "Expected behaviour",
            "",
            "Low",
            "",
            "",
            "",
            "",
            "2026-09-03T00:00:00Z",
        ),
        (
            "D-RISK",
            "Navigation fails for the driver",
            risk_description,
            json.dumps(
                [
                    {
                        "text": "The failure reproduces after a power cycle.",
                        "creation_time": "2025-01-03T00:02:00Z",
                        "author": {"full_name": "Fixture Tester"},
                    }
                ]
            ),
            "2025-01-02T00:00:00Z",
            "2025",
            "IDCEVO",
            "09-Concluded without action",
            "Navigation",
            "",
            "Management decision",
            "",
            "Medium",
            "05-unsatisfactory",
            "Showstopper_Candidate",
            "",
            "",
            "2026-09-03T00:00:00Z",
        ),
        (
            "D-DUP",
            "Duplicate of repaired parent",
            "Same issue as the linked parent.",
            "[]",
            "2025-01-03T00:00:00Z",
            "2025",
            "IDCEVO",
            "09-Concluded without action",
            "Entertainment",
            "",
            "Child (Duplicate)",
            "",
            "Medium",
            "",
            "",
            "Child",
            "D-PARENT",
            "2026-09-03T00:00:00Z",
        ),
        (
            "D-PLAN",
            "A fix has a release plan",
            "Issue is deferred pending the planned release.",
            json.dumps(
                [
                    {
                        "text": "Fix is planned for release 26-07.",
                        "creation_time": "2025-01-07T00:00:00Z",
                    }
                ]
            ),
            "2025-01-04T00:00:00Z",
            "2025",
            "IDCEVO",
            "09-Concluded without action",
            "Wireless Services",
            "",
            "Tolerated",
            "1",
            "Medium",
            "",
            "",
            "",
            "",
            "2026-09-03T00:00:00Z",
        ),
        (
            "D-CHINA",
            "China-only speech ticket",
            "This must be excluded from the Global report.",
            "[]",
            "2025-01-05T00:00:00Z",
            "2025",
            "IDCEVO",
            "09-Concluded without action",
            "Speech CN",
            "",
            "Management decision",
            "",
            "High",
            "06-customer irritated",
            "",
            "",
            "",
            "2026-09-03T00:00:00Z",
        ),
        (
            "D-COC",
            "CoC development-team ticket",
            "This must be excluded because the development team raised it.",
            "[]",
            "2025-01-06T00:00:00Z",
            "2025",
            "IDCEVO",
            "09-Concluded without action",
            "Core UI",
            "",
            "Not reproducible",
            "",
            "Medium",
            "",
            "",
            "",
            "",
            "2026-09-03T00:00:00Z",
        ),
        (
            "D-COC-TEAM",
            "CoC assigned-team ticket",
            "This remains in scope because the finder team is not CoC.",
            "[]",
            "2025-01-07T00:00:00Z",
            "2025",
            "IDCEVO",
            "09-Concluded without action",
            "Core UI",
            "",
            "Not reproducible",
            "",
            "Medium",
            "",
            "",
            "",
            "",
            "2026-09-03T00:00:00Z",
        ),
        (
            "D-PARENT",
            "Parent was resolved",
            "The duplicate parent has a confirmed resolution.",
            "[]",
            "2025-01-01T00:00:00Z",
            "2025",
            "IDCEVO",
            "06-Concluded",
            "Entertainment",
            "",
            "",
            "",
            "Medium",
            "",
            "",
            "Parent",
            "",
            "2026-09-03T00:00:00Z",
        ),
    ]

    conn = sqlite3.connect(db_path)
    try:
        conn.executemany(
            """
            INSERT INTO octane_defects(
                defect_id, name, description, comments, creation_time, year, project,
                phase, solution_cluster, defect_category, blocking_reason,
                reprel_changes, severity, problem_severity, reporting_class,
                parent_child_type, relation_to, fetched_at, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}')
            """,
            defects,
        )
        conn.execute(
            "UPDATE octane_defects SET problem_finder_team = ? WHERE defect_id = ?",
            ("[AT]CoC_EI_IuK", "D-COC"),
        )
        conn.execute(
            """
            UPDATE octane_defects
            SET problem_finder_team = ?, team = ?
            WHERE defect_id = ?
            """,
            ("FIT", "IuK_CoC_Connectivity", "D-COC-TEAM"),
        )
        conn.execute(
            """
            UPDATE octane_defects
            SET solution_cluster = ?, product_areas = ?
            WHERE defect_id = ?
            """,
            ("", "Provide Navigation 2.0", "D-RISK"),
        )
        conn.executemany(
            """
            INSERT INTO octane_defect_history_events(
                defect_id, event_timestamp, field_name, old_value, new_value, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                ("D-LOW", "2025-01-02T00:00:00Z", "phase", "01-New", "09-Concluded without action", "2026-09-03T00:00:00Z"),
                ("D-RISK", "2025-01-03T00:00:00Z", "phase", "01-New", "09-Concluded without action", "2026-09-03T00:00:00Z"),
                ("D-DUP", "2025-01-04T00:00:00Z", "phase", "01-New", "09-Concluded without action", "2026-09-03T00:00:00Z"),
                ("D-PLAN", "2025-01-05T00:00:00Z", "phase", "01-New", "09-Concluded without action", "2026-09-03T00:00:00Z"),
                ("D-PARENT", "2025-01-06T00:00:00Z", "phase", "04-In Progress", "06-Concluded", "2026-09-03T00:00:00Z"),
                ("D-PLAN", "2025-01-08T00:00:00Z", "requested_target_i_step_release_udf", "", "R-25-10", "2026-09-03T00:00:00Z"),
                ("D-PLAN", "2025-01-09T00:00:00Z", "com_sol_avail_date_udf", "", "2025-03-01T00:00:00Z", "2026-09-03T00:00:00Z"),
                ("D-CHINA", "2025-01-06T00:00:00Z", "phase", "01-New", "09-Concluded without action", "2026-09-03T00:00:00Z"),
                ("D-COC", "2025-01-07T00:00:00Z", "phase", "01-New", "09-Concluded without action", "2026-09-03T00:00:00Z"),
                ("D-COC-TEAM", "2025-01-08T00:00:00Z", "phase", "01-New", "09-Concluded without action", "2026-09-03T00:00:00Z"),
                ("D-LOW", "2025-01-02T00:01:00Z", "rejected_status_udf", "", "09 Rejected by CoC", "2026-09-03T00:00:00Z"),
                ("D-RISK", "2025-01-03T00:01:00Z", "blocking_reason_udf", "", "Management decision", "2026-09-03T00:00:00Z"),
                (
                    "D-RISK",
                    "2025-01-03T00:01:00Z",
                    "postpone_tolerate_doc_udf",
                    "",
                    "<html><body>Event chain-overlapping error description from the CoC perspective: Error recovery / frequency prognosis based on development / field data: Customer impact (short / permanent malfunction indication, CCM...): Diagnostic impact (expected DTCs): Instructions for plant process (e.g.: false errors causing rework, prolonging the process time, DTC, BA/NAEL): Impact and instructions for sales organization (extra service instructions necessary, ZEDIS updates, etc.): Documentation of the Decision &lt;DATE&gt;.: Function Owner/CoC: Plant: validation intensive testing: Aftersales: M50 Management: RSU / System function: E/E quality (AQ/ZX-Q):</body></html>",
                    "2026-09-03T00:00:00Z",
                ),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    result = generate_phase09_idcevo_report(db_path=db_path, output_path=output_path)

    assert result == output_path
    html = output_path.read_text(encoding="utf-8")
    payload = _report_payload(html)
    assert payload["summary"]["idcevo_all_ticket_total"] == 8
    assert payload["summary"]["idcevo_current_phase09_total"] == 7
    assert payload["summary"]["idcevo_phase09_total"] == 7
    assert payload["summary"]["global_phase09_total"] == 5
    assert payload["summary"]["china_excluded_total"] == 1
    assert payload["summary"]["coc_development_team_excluded_total"] == 1
    assert payload["summary"]["direct_close_total"] == 5
    assert payload["management_decision"]["total"] == 1
    assert payload["management_decision"]["priority_review_total"] == 1
    assert payload["management_decision"]["no_repair_evidence_total"] == 1
    assert payload["closure_evidence"]["target_version_after_phase09_total"] == 1
    assert payload["closure_evidence"]["explicit_linked_resolution_after_phase09_total"] == 1
    assert payload["closure_evidence"]["no_target_shift_or_explicit_resolution_total"] == 3
    assert payload["distributions"]["problem_severity"]

    tickets = {ticket["ticket_id"]: ticket for ticket in payload["tickets"]}
    assert "D-CHINA" not in tickets
    assert "D-COC" not in tickets
    assert "D-COC-TEAM" in tickets
    assert tickets["D-LOW"]["rejection_route"] == "09 Rejected by CoC"
    assert tickets["D-LOW"]["risk_tier"] == "Normal closure evidence"
    assert tickets["D-DUP"]["risk_tier"] == "Normal closure evidence"
    assert tickets["D-DUP"]["duplicate_parent_phase"] == "06-Concluded"
    assert tickets["D-DUP"]["related_resolution_status"] == "Explicit linked ticket reached Phase 06 after current Phase 09"
    assert tickets["D-DUP"]["related_resolution_phase06_at"] == "2025-01-06T00:00:00Z"
    assert tickets["D-RISK"]["risk_tier"] == "Priority production-exposure review"
    assert tickets["D-RISK"]["direct_close"] is True
    assert tickets["D-RISK"]["effective_reason"] == "Management decision"
    assert tickets["D-RISK"]["target_version_status"] == "No target version recorded"
    assert tickets["D-RISK"]["reprel_change_status"] == "Reprel change count not recorded"
    assert tickets["D-RISK"]["production_exposure_status"] == "Production exposure cannot be ruled out - priority review"
    assert tickets["D-RISK"]["postponement_document_status"] == "Template only"
    assert tickets["D-RISK"]["has_substantive_assessment_document"] is False
    assert tickets["D-RISK"]["has_follow_up_evidence"] is False
    assert tickets["D-RISK"]["is_governed_severe"] is True
    assert tickets["D-RISK"]["needs_closure_evidence_review"] is True
    assert tickets["D-RISK"]["closure_disposition"] == "Direct closure without auditable rationale or follow-up"
    assert tickets["D-LOW"]["closure_disposition"] == "Acceptable closure evidence"
    assert tickets["D-PLAN"]["repair_plan_status"] == "Explicit post-closure repair plan"
    assert tickets["D-PLAN"]["target_version_status"] == "Target version recorded after current Phase 09"
    assert tickets["D-PLAN"]["target_version_value"] == "R-25-10"
    assert tickets["D-PLAN"]["reprel_change_status"] == "Recorded Reprel changes: 1"
    assert tickets["D-PLAN"]["commercial_solution_date_status"] == "Commercial solution date recorded after current Phase 09"
    assert tickets["D-PLAN"]["has_follow_up_evidence"] is True
    assert tickets["D-PLAN"]["closure_disposition"] == "Follow-up / remediation recorded"
    assert "IDCEVO Phase 09 Global Closure Risk Review" in html
    assert "Priority production-exposure review" in html
    assert "Release, Shift, and Related Resolution Evidence" in html
    assert "All IDCEVO tickets created from 2025-01-01" in html
    assert "excludes China-specific functions and tickets raised by CoC development teams" in html
    assert "final rejection route 09 Rejected by CoC are excluded" not in html

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE octane_defects SET solution_cluster = ? WHERE defect_id = ?",
            ("Speech CN", "D-COC"),
        )
        conn.commit()
    finally:
        conn.close()

    inclusive_output_path = tmp_path / "idcevo_phase09_all_scope_risk.html"
    generate_phase09_idcevo_report(
        db_path=db_path,
        output_path=inclusive_output_path,
        include_china_specific=True,
    )
    inclusive_html = inclusive_output_path.read_text(encoding="utf-8")
    inclusive_payload = _report_payload(inclusive_html)
    inclusive_tickets = {ticket["ticket_id"] for ticket in inclusive_payload["tickets"]}
    assert inclusive_payload["summary"]["global_phase09_total"] == 6
    assert inclusive_payload["summary"]["china_excluded_total"] == 0
    assert inclusive_payload["summary"]["china_specific_in_scope_total"] == 1
    assert "D-CHINA" in inclusive_tickets
    assert "<title>IDCEVO Phase 09 Closure Risk Review</title>" in inclusive_html
    assert "includes China-specific functions and excludes tickets whose Problem Finder Team contains CoC" in inclusive_html
    assert "Closure Disposition" in inclusive_html

    workbook_path = tmp_path / "idcevo_phase09_closure_risk.xlsx"
    llm_summaries_path = tmp_path / "phase09_llm_summaries.jsonl"
    llm_summaries_path.write_text(
        json.dumps(
            {
                "ticket_id": "D-RISK",
                "summary": (
                    "问题：导航功能在可复现条件下失败。\n"
                    "处理过程：当前记录直接由 Phase 01 进入 Phase 09。\n"
                    "Reject 原因：Management decision。\n"
                    "闭环证据：未记录目标版本、计划窗口、商业解决日期或 Reprel Shift。\n"
                    "风险判断：Showstopper Candidate + BI05 且无可审计闭环，需核实是否影响生产/客户现场。"
                ),
                "model": "fixture-model",
                "source": "llm",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    generate_phase09_idcevo_excel_report(
        db_path=db_path,
        output_path=workbook_path,
        include_china_specific=True,
        llm_summaries_path=llm_summaries_path,
    )
    workbook = load_workbook(workbook_path, read_only=True, data_only=True)
    assert workbook.sheetnames == [
        "00_总览",
        "01_拒绝原因",
        "02_功能风险",
        "03_优先复核",
        "04_严重度复核",
        "05_无证据直关",
        "06_全部明细",
        "07_判定口径",
        "08_证据原文",
    ]
    assert workbook["00_总览"]["A1"].value == "IDCEVO Phase 09 Closure Risk Review"
    assert workbook["00_总览"]["B9"].value == 6

    priority_sheet = workbook["03_优先复核"]
    priority_headers = [cell.value for cell in next(priority_sheet.iter_rows(max_row=1))]
    priority_ticket_index = priority_headers.index("Ticket ID")
    priority_function_index = priority_headers.index("功能")
    priority_analysis_index = priority_headers.index("LLM 处理摘要")
    priority_summary_source_index = priority_headers.index("摘要来源")
    priority_risk_basis_index = priority_headers.index("风险等级依据")
    priority_evidence_index = priority_headers.index("证据原文索引")
    priority_ticket_ids = {
        row[priority_ticket_index]
        for row in priority_sheet.iter_rows(min_row=2, values_only=True)
    }
    assert "D-RISK" in priority_ticket_ids
    priority_functions_by_ticket_id = {
        row[priority_ticket_index]: row[priority_function_index]
        for row in priority_sheet.iter_rows(min_row=2, values_only=True)
    }
    assert priority_functions_by_ticket_id["D-RISK"] == "Provide Navigation 2.0"
    priority_context_by_ticket_id = {
        row[priority_ticket_index]: {
            "analysis": row[priority_analysis_index],
            "summary_source": row[priority_summary_source_index],
            "risk_basis": row[priority_risk_basis_index],
            "evidence_index": row[priority_evidence_index],
        }
        for row in priority_sheet.iter_rows(min_row=2, values_only=True)
    }
    assert "Reject 原因：Management decision" in priority_context_by_ticket_id["D-RISK"]["analysis"]
    assert "Observed failure remains reproducible." not in priority_context_by_ticket_id["D-RISK"]["analysis"]
    assert priority_context_by_ticket_id["D-RISK"]["summary_source"] == "LLM / fixture-model"
    assert "Showstopper_Candidate / 05-unsatisfactory" in priority_context_by_ticket_id["D-RISK"]["risk_basis"]
    assert "No remediation or follow-up evidence" in priority_context_by_ticket_id["D-RISK"]["risk_basis"]
    assert "08_证据原文" in priority_context_by_ticket_id["D-RISK"]["evidence_index"]

    direct_close_sheet = workbook["05_无证据直关"]
    direct_close_headers = [cell.value for cell in next(direct_close_sheet.iter_rows(max_row=1))]
    direct_close_ticket_index = direct_close_headers.index("Ticket ID")
    direct_close_ticket_ids = {
        row[direct_close_ticket_index]
        for row in direct_close_sheet.iter_rows(min_row=2, values_only=True)
    }
    assert "D-RISK" in direct_close_ticket_ids

    detail_sheet = workbook["06_全部明细"]
    detail_headers = [cell.value for cell in next(detail_sheet.iter_rows(max_row=1))]
    detail_ticket_index = detail_headers.index("Ticket ID")
    detail_ticket_ids = {
        row[detail_ticket_index]
        for row in detail_sheet.iter_rows(min_row=2, values_only=True)
    }
    assert "D-CHINA" in detail_ticket_ids

    evidence_sheet = workbook["08_证据原文"]
    evidence_headers = [cell.value for cell in next(evidence_sheet.iter_rows(max_row=1))]
    evidence_ticket_index = evidence_headers.index("Ticket ID")
    evidence_type_index = evidence_headers.index("证据类型")
    evidence_actor_index = evidence_headers.index("作者/操作者")
    evidence_field_index = evidence_headers.index("来源字段")
    evidence_text_index = evidence_headers.index("原文内容")
    evidence_chunk_index = evidence_headers.index("分块序号")
    evidence_rows = list(evidence_sheet.iter_rows(min_row=2, values_only=True))
    description_evidence_rows = sorted(
        (
            row
            for row in evidence_rows
            if row[evidence_ticket_index] == "D-RISK"
            and row[evidence_type_index] == "Ticket description"
        ),
        key=lambda row: row[evidence_chunk_index],
    )
    assert len(description_evidence_rows) > 1
    assert "".join(
        str(row[evidence_text_index]) for row in description_evidence_rows
    ) == risk_description
    assert any(
        row[evidence_ticket_index] == "D-RISK"
        and row[evidence_type_index] == "Ticket comment"
        and row[evidence_actor_index] == "Fixture Tester"
        and "The failure reproduces after a power cycle." in str(row[evidence_text_index])
        for row in evidence_rows
    )
    assert any(
        row[evidence_ticket_index] == "D-RISK"
        and row[evidence_type_index] == "Closure history"
        and "blocking_reason_udf" in str(row[evidence_field_index])
        and "Management decision" in str(row[evidence_text_index])
        for row in evidence_rows
    )