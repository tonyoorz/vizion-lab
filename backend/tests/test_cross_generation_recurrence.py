import json
from datetime import datetime

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

from backend.analytics import cross_generation_recurrence


def test_build_all_function_analysis_keeps_direct_and_exact_evidence_separate() -> None:
    records = [
        {
            "defect_id": "D-MGU-EXACT",
            "project": "MGU",
            "tproject": "MGU",
            "creation_time": "2026-01-01T00:00:00Z",
            "name": "CarPlay disconnects after vehicle restart",
            "product_areas": "CarPlay [01.04.01.07.02]",
            "assigned_ecu": "HU-MGU_02_L",
            "aida_businesskey": "",
            "relation_to": "",
        },
        {
            "defect_id": "D-IDCEVO-EXACT",
            "project": "IDCEVO",
            "tproject": "IDCEVO",
            "creation_time": "2026-01-22T00:00:00Z",
            "name": "IDCEVO CarPlay disconnects after vehicle restart",
            "product_areas": "CarPlay [01.04.01.07.02]",
            "assigned_ecu": "IDCEVO-25",
            "aida_businesskey": "",
            "relation_to": "",
        },
        {
            "defect_id": "D-IDC-REL",
            "project": "IDC",
            "tproject": "IDC",
            "creation_time": "2026-02-01T00:00:00Z",
            "name": "Display freezes after a profile switch",
            "product_areas": "Display Settings [01.04.02.03.01.01.07.01]",
            "assigned_ecu": "HU-MGU_02_A",
            "aida_businesskey": "",
            "relation_to": "REL-42",
        },
        {
            "defect_id": "D-IDCEVO-REL",
            "project": "IDCEVO",
            "tproject": "IDCEVO",
            "creation_time": "2026-02-22T00:00:00Z",
            "name": "Screen does not resume after user change",
            "product_areas": "Display Settings [01.04.02.03.01.01.07.01]",
            "assigned_ecu": "IDCEVO-25",
            "aida_businesskey": "",
            "relation_to": "REL-42",
        },
        {
            "defect_id": "D-MGU-BROAD",
            "project": "MGU",
            "tproject": "MGU",
            "creation_time": "2026-03-01T00:00:00Z",
            "name": "Shared title must not become a recurrence from a broad label",
            "product_areas": "Stability",
            "assigned_ecu": "HU-MGU_02_L",
            "aida_businesskey": "",
            "relation_to": "",
        },
        {
            "defect_id": "D-IDCEVO-BROAD",
            "project": "IDCEVO",
            "tproject": "IDCEVO",
            "creation_time": "2026-03-22T00:00:00Z",
            "name": "Shared title must not become a recurrence from a broad label",
            "product_areas": "Stability",
            "assigned_ecu": "IDCEVO-25",
            "aida_businesskey": "",
            "relation_to": "",
        },
    ]
    builder = getattr(cross_generation_recurrence, "build_all_function_recurrence_analysis", None)
    assert callable(builder), "The all-function recurrence builder is not implemented"

    analysis = builder(records, minimum_lag_days=14)

    assert analysis["summary"]["comparable_ticket_count"] == 6
    assert analysis["summary"]["shared_function_count"] == 3
    assert analysis["summary"]["candidate_count"] == 2
    assert {candidate["candidate_kind"] for candidate in analysis["candidates"]} == {
        "direct_relation_reference",
        "exact_normalized_title_in_shared_function",
    }
    assert {candidate["service_function"] for candidate in analysis["candidates"]} == {
        "CarPlay [01.04.01.07.02]",
        "Display Settings [01.04.02.03.01.01.07.01]",
    }
    stability = next(item for item in analysis["function_catalog"] if item["service_function"] == "Stability")
    assert stability["candidate_count"] == 0
    assert stability["semantic_matching_allowed"] is False
    assert all(candidate["review_status"] == "human_review_required" for candidate in analysis["candidates"])


@pytest.fixture
def all_function_export_inputs(tmp_path):
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
    template_path = tmp_path / "template.xlsx"
    template.save(template_path)
    template.close()
    records = [
        {
            "defect_id": "D-MGU",
            "project": "MGU",
            "tproject": "MGU",
            "creation_time": "2026-01-01T08:00:00+08:00",
            "name": "CarPlay disconnects after vehicle restart",
            "product_areas": "CarPlay [01.04.01.07.02]",
            "assigned_ecu": "HU-MGU_02_L",
            "aida_businesskey": "",
            "relation_to": "",
        },
        {
            "defect_id": "D-IDCEVO",
            "project": "IDCEVO",
            "tproject": "IDCEVO",
            "creation_time": "2026-01-22T00:00:00Z",
            "name": "IDCEVO CarPlay disconnects after vehicle restart",
            "product_areas": "CarPlay [01.04.01.07.02]",
            "assigned_ecu": "IDCEVO-25",
            "aida_businesskey": "",
            "relation_to": "",
        },
    ]
    builder = getattr(cross_generation_recurrence, "build_all_function_recurrence_analysis", None)
    if callable(builder):
        analysis = builder(records, minimum_lag_days=14)
    else:
        analysis = {"schema_version": "cross-generation-recurrence-v1"}
    analysis_path = tmp_path / "analysis.json"
    analysis_path.write_text(json.dumps(analysis), encoding="utf-8")
    return analysis_path, template_path, tmp_path / "report.xlsx", sheet_names


def test_all_function_excel_export_lists_catalog_and_review_candidates(all_function_export_inputs) -> None:
    analysis_path, template_path, output_path, sheet_names = all_function_export_inputs
    exporter = getattr(cross_generation_recurrence, "export_all_function_recurrence_excel", None)
    assert callable(exporter), "The all-function Excel exporter is not implemented"

    assert exporter(
        analysis_json=analysis_path,
        template_xlsx=template_path,
        output_xlsx=output_path,
    ) == output_path

    workbook = load_workbook(output_path)
    assert workbook.sheetnames == sheet_names
    assert workbook.worksheets[0]["A1"].fill.fgColor.rgb == "FF1F4E78"
    scenarios = workbook["01_Scenario_Library"]
    catalog = workbook["03_Service_Function_Catalog"]
    mapping = workbook["07_Ticket_Mapping"]
    assert scenarios.max_row == 2
    assert catalog.max_row == 2
    assert mapping.max_row == 3
    scenario_headers = [cell.value for cell in scenarios[1]]
    assert scenarios.cell(2, scenario_headers.index("Candidate Evidence") + 1).value == "Exact normalized title + shared function"
    mapping_headers = [cell.value for cell in mapping[1]]
    created_at = mapping.cell(2, mapping_headers.index("Ticket Created UTC") + 1)
    assert created_at.value == datetime(2026, 1, 1)
    assert created_at.number_format == "yyyy-mm-dd hh:mm"
    assert scenarios.sheet_view.showGridLines is False
    workbook.close()