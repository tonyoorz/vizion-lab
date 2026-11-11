import json
from datetime import datetime

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

from backend.analytics import cross_generation_mybmw_recurrence
from backend.analytics.cross_generation_mybmw_recurrence import (
    build_forward_recurrence_candidates,
    infer_explicit_host_generations,
)


def test_infer_explicit_host_generations_recognizes_underscore_delimited_tags() -> None:
    record = {
        "name": "MyBMW_MGU21 regression also observed on IDC23",
        "project": "App",
        "tproject": "App",
    }

    assert infer_explicit_host_generations(record) == {"MGU", "IDC"}


def test_build_forward_recurrence_candidates_requires_lag_and_function_evidence() -> None:
    records = [
        {
            "defect_id": "D-MGU",
            "project": "MGU",
            "tproject": "App",
            "creation_time": "2026-01-01T00:00:00Z",
            "name": "MyBMW indoor parking is not displayed in app",
            "assigned_ecu": "APP_Mobile_2_0",
            "product_areas": "Parking",
            "aida_businesskey": "",
            "software_version": "",
        },
        {
            "defect_id": "D-SAME-DAY",
            "project": "IDCEVO",
            "tproject": "App",
            "creation_time": "2026-01-01T00:00:00Z",
            "name": "MyBMW IDCEVO indoor parking is not displayed in app",
            "assigned_ecu": "APP_Mobile_2_0",
            "product_areas": "Parking",
            "aida_businesskey": "",
            "software_version": "",
        },
        {
            "defect_id": "D-IDCEVO",
            "project": "IDCEVO",
            "tproject": "App",
            "creation_time": "2026-01-22T00:00:00Z",
            "name": "MyBMW IDCEVO indoor parking is not displayed in app",
            "assigned_ecu": "APP_Mobile_2_0",
            "product_areas": "Parking",
            "aida_businesskey": "",
            "software_version": "",
        },
        {
            "defect_id": "D-UNRELATED",
            "project": "IDCEVO",
            "tproject": "App",
            "creation_time": "2026-02-01T00:00:00Z",
            "name": "MyBMW account login QR code is invalid",
            "assigned_ecu": "OTHER_ECU",
            "product_areas": "Account",
            "aida_businesskey": "",
            "software_version": "",
        },
    ]

    candidates = build_forward_recurrence_candidates(
        records,
        minimum_lag_days=14,
        minimum_similarity=0.70,
    )

    assert [(item["source_ticket_id"], item["target_ticket_id"]) for item in candidates] == [
        ("D-MGU", "D-IDCEVO"),
    ]
    assert candidates[0]["source_host"] == "MGU"
    assert candidates[0]["target_host"] == "IDCEVO"
    assert candidates[0]["lag_days"] == 21
    assert "product_areas" in candidates[0]["evidence"]


@pytest.fixture
def excel_export_inputs(tmp_path):
    sheet_names = [
        "00_\u603b\u89c8",
        "01_Scenario_Library",
        "02_Scenario_FoundIn_Function",
        "03_Service_Function_Catalog",
        "04_Decision_Tree_Catalog",
        "05_Decision_Tree_Nodes",
        "06_Service_Future_Plan_Cards",
        "07_Ticket_Mapping",
        "08_Data_Quality",
        "09_Data_Dictionary",
    ]
    template = Workbook()
    template.remove(template.active)
    for sheet_name in sheet_names:
        sheet = template.create_sheet(sheet_name)
        sheet.append(["Template Header", "Template Header"])
        sheet.append(["Template-only content", "Template-only content"])
        for cell in sheet[1]:
            cell.font = Font(name="Calibri", size=11, bold=True, color="FFFFFFFF")
            cell.fill = PatternFill("solid", fgColor="FF1F4E78")
    template.worksheets[0]["A1"].fill = PatternFill()
    template.worksheets[0]["A1"].font = Font(name="Calibri", size=16, bold=True)
    template_path = tmp_path / "template.xlsx"
    template.save(template_path)
    template.close()
    payload = {
        "schema_version": "cross-generation-mybmw-recurrence-v1",
        "generated_at": "2026-10-19T09:43:26Z",
        "source": {
            "database_path": "database/source/qgate_raw.db",
            "max_creation_time": "2026-01-22T00:00:00Z",
            "total_defect_rows": 10,
        },
        "summary": {
            "strict_mybmw_ticket_count": 5,
            "single_explicit_host_ticket_count": {"MGU": 1, "IDC": 0, "IDCEVO": 1},
            "multi_host_records_excluded_from_pairing": 1,
            "no_explicit_host_records_excluded_from_pairing": 2,
            "forward_recurrence_candidate_count": 1,
            "candidate_count_by_transition": {"MGU->IDCEVO": 1},
        },
        "method": {"minimum_title_similarity": 0.70, "minimum_forward_lag_days": 14},
        "supervised_prediction_readiness": {"ready": False},
        "candidates": [
            {
                "source_ticket_id": "2602721",
                "target_ticket_id": "2799336",
                "source_host": "MGU",
                "target_host": "IDCEVO",
                "source_creation_time": "2026-01-01T08:00:00+08:00",
                "target_creation_time": "2026-01-22T00:00:00Z",
                "source_title": "=1+1",
                "target_title": "MyBMW indoor parking is not displayed",
                "source_assigned_ecu": "APP_Mobile_2_0",
                "target_assigned_ecu": "APP_Mobile_2_0",
                "source_product_areas": "Parking",
                "target_product_areas": "Parking",
                "lag_days": 21,
                "similarity": 0.91,
                "evidence": ["assigned_ecu", "product_areas"],
                "review_status": "human_review_required",
            }
        ],
    }
    analysis_path = tmp_path / "analysis.json"
    analysis_path.write_text(json.dumps(payload), encoding="utf-8")
    return analysis_path, template_path, tmp_path / "report.xlsx", sheet_names


def test_excel_export_reuses_template_layout_and_preserves_sources(excel_export_inputs) -> None:
    analysis_path, template_path, output_path, sheet_names = excel_export_inputs
    original_analysis = analysis_path.read_bytes()
    original_template = template_path.read_bytes()
    exporter = getattr(cross_generation_mybmw_recurrence, "export_recurrence_excel", None)
    assert callable(exporter), "The recurrence Excel exporter is not implemented"

    assert exporter(
        analysis_json=analysis_path,
        template_xlsx=template_path,
        output_xlsx=output_path,
    ) == output_path

    workbook = load_workbook(output_path)
    assert workbook.sheetnames == sheet_names
    assert workbook.worksheets[0]["A1"].fill.fgColor.rgb == "FF1F4E78"
    assert workbook.worksheets[0]["A1"].fill.patternType == "solid"
    scenarios = workbook["01_Scenario_Library"]
    headers = [cell.value for cell in scenarios[1]]
    similarity = scenarios.cell(2, headers.index("Title Similarity") + 1)
    assert scenarios.max_row == 2
    assert scenarios.max_column == 12
    assert similarity.value == 0.91
    assert similarity.number_format == "0.0%"
    assert scenarios["A1"].fill.fgColor.rgb == "FF1F4E78"
    assert scenarios["A1"].font.name == "Calibri"
    assert scenarios.freeze_panes == "D2"
    assert scenarios.auto_filter.ref == "A1:L2"
    assert scenarios.sheet_view.showGridLines is False

    mapping = workbook["07_Ticket_Mapping"]
    mapping_headers = [cell.value for cell in mapping[1]]
    created_at = mapping.cell(2, mapping_headers.index("Ticket Created UTC") + 1)
    title = mapping.cell(2, mapping_headers.index("Ticket Title") + 1)
    assert mapping.max_row == 3
    assert created_at.value == datetime(2026, 1, 1)
    assert created_at.number_format == "yyyy-mm-dd hh:mm"
    assert title.value == "=1+1" and title.data_type == "s"
    assert all(
        cell.value != "Template-only content"
        for sheet in workbook.worksheets
        for row in sheet.iter_rows()
        for cell in row
    )
    workbook.close()
    assert analysis_path.read_bytes() == original_analysis
    assert template_path.read_bytes() == original_template


def test_excel_export_refuses_to_overwrite_an_existing_workbook(excel_export_inputs) -> None:
    analysis_path, template_path, _, _ = excel_export_inputs
    exporter = getattr(cross_generation_mybmw_recurrence, "export_recurrence_excel", None)
    assert callable(exporter), "The recurrence Excel exporter is not implemented"

    with pytest.raises(FileExistsError):
        exporter(
            analysis_json=analysis_path,
            template_xlsx=template_path,
            output_xlsx=template_path,
        )