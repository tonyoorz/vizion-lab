import json
from pathlib import Path
import subprocess
import sys

from openpyxl import Workbook

from backend.analytics.phase09_scenario_diagnosis_tree_catalog import (
    build_scenario_diagnosis_catalog_from_files,
    build_scenario_diagnosis_path,
    load_v12_scenario_paths,
    render_scenario_diagnosis_catalog_html,
    write_scenario_diagnosis_catalog_artifacts,
)


def _source_graph_with_complete_leaf() -> dict:
    return {
        "tree_id": "CARPLAY",
        "reference_tree_id": "DTREF-CARPLAY",
        "source_asset": "decision_tree_sources/lossless_webp/carplay.webp",
        "nodes": [
            {
                "node_id": "ROOT",
                "parent_node_id": "",
                "node_type": "start",
                "source_text": "CarPlay",
                "source_path": "CARPLAY",
                "is_terminal": False,
            },
            {
                "node_id": "SRC-CARPLAY-USB",
                "parent_node_id": "ROOT",
                "node_type": "resolution",
                "source_text": "USB wired connection failure",
                "source_path": "CARPLAY > USB connection failure",
                "symptom": "CarPlay cannot connect through USB",
                "possible_cause": "USB cable or vehicle USB port fault",
                "check_step": "Check the USB cable and test another vehicle USB port.",
                "solution": "Replace the data-capable cable or repair the USB port.",
                "escalation_path": "L1 to L3 dealer diagnostics",
                "is_terminal": True,
            },
        ],
        "edges": [
            {
                "from_node_id": "ROOT",
                "to_node_id": "SRC-CARPLAY-USB",
                "branch_outcome": "NEXT",
            }
        ],
    }


def test_scenario_path_preserves_the_exact_failure_sequence() -> None:
    scenario = {
        "service_function_id": "CARPLAY",
        "service_function": "CarPlay",
        "service_function_label": "CarPlay",
        "scenario_id": "FS-0001",
        "customer_symptom": "CarPlay cannot connect through USB",
        "trigger_condition": "After the driver plugs in the iPhone cable",
        "observed_behaviour": "The head unit never recognizes CarPlay",
        "expected_behaviour": "CarPlay should start after USB connection",
        "rep_title": "USB CarPlay connection failure",
        "ticket_ids": ["D-1"],
        "ticket_count": 1,
        "recovery_action": "",
        "evidence_confidence": "high",
    }

    path = build_scenario_diagnosis_path(
        scenario=scenario,
        source_graphs=[_source_graph_with_complete_leaf()],
        ticket_evidence_by_id={"D-1": {"rca_root_cause": ""}},
    )

    assert path["scenario_id"] == "FS-0001"
    assert path["customer_symptom"] == "CarPlay cannot connect through USB"
    assert path["trigger_condition"] == "After the driver plugs in the iPhone cable"
    assert [node["node_type"] for node in path["context_nodes"]] == [
        "Function Entry",
        "Customer Symptom",
        "Trigger Condition",
        "Observed Behaviour",
    ]
    experience_path = next(
        candidate
        for candidate in path["diagnostic_branches"]
        if candidate["basis"] == "after_sales_experience_source_path"
    )
    assert experience_path["possible_cause"] == "USB cable or vehicle USB port fault"
    assert experience_path["check_steps"] == (
        "Check the USB cable and test another vehicle USB port."
    )
    assert experience_path["resolution"] == (
        "Replace the data-capable cable or repair the USB port."
    )
    assert experience_path["escalation"] == "L1 to L3 dealer diagnostics"
    assert experience_path["status"] == "source_experience_reference_candidate"
    assert experience_path["source_reference"]["source_node_id"] == "SRC-CARPLAY-USB"


def test_scenario_path_keeps_ticket_rca_unverified_and_falls_back_to_evidence_collection() -> None:
    scenario = {
        "service_function_id": "AI_TRAVEL_COMPANION",
        "service_function": "AI Empowered Travel Companion",
        "service_function_label": "AI Empowered Travel Companion",
        "scenario_id": "FS-0002",
        "customer_symptom": "Travel companion search times out",
        "trigger_condition": "After the user asks for a nearby charging station",
        "observed_behaviour": "No recommended destination is returned",
        "expected_behaviour": "A charging station recommendation is returned",
        "rep_title": "Travel companion timeout",
        "ticket_ids": ["D-2"],
        "ticket_count": 1,
        "recovery_action": "Restart the application",
        "evidence_confidence": "high",
    }

    path = build_scenario_diagnosis_path(
        scenario=scenario,
        source_graphs=[],
        ticket_evidence_by_id={
            "D-2": {"rca_root_cause": "Backend search service request timeout"}
        },
    )

    rca_path = next(
        candidate
        for candidate in path["diagnostic_branches"]
        if candidate["basis"] == "ticket_recorded_rca_candidate"
    )
    assert rca_path["possible_cause"] == "Backend search service request timeout"
    assert rca_path["status"] == "ticket_recorded_candidate_review_required"
    assert rca_path["resolution"] == "Restart the application"
    assert rca_path["resolution_status"] == "ticket_recorded_workaround_not_approved"
    assert "Validate the ticket-recorded candidate" in rca_path["check_steps"]
    assert not any(
        candidate["basis"] == "after_sales_experience_source_path"
        for candidate in path["diagnostic_branches"]
    )


def test_scenario_path_rejects_conflicting_low_support_ticket_rca_candidates() -> None:
    scenario = {
        "service_function_id": "CARPLAY",
        "service_function": "CarPlay",
        "service_function_label": "CarPlay",
        "scenario_id": "FS-0002B",
        "customer_symptom": "CarPlay setting keeps loading",
        "trigger_condition": "After opening the CarPlay setting",
        "observed_behaviour": "The setting never finishes loading",
        "expected_behaviour": "The setting is available",
        "rep_title": "CarPlay setting loading",
        "ticket_ids": ["D-1", "D-2", "D-3", "D-4", "D-5", "D-6"],
        "ticket_count": 6,
        "recovery_action": "",
        "evidence_confidence": "high",
    }

    path = build_scenario_diagnosis_path(
        scenario=scenario,
        source_graphs=[],
        ticket_evidence_by_id={
            "D-1": {"rca_root_cause": "Vehicle gateway session timeout"},
            "D-2": {"rca_root_cause": "Navigation backend response mismatch"},
            "D-3": {"rca_root_cause": ""},
            "D-4": {"rca_root_cause": ""},
            "D-5": {"rca_root_cause": ""},
            "D-6": {"rca_root_cause": ""},
        },
    )

    assert [branch["basis"] for branch in path["diagnostic_branches"]] == [
        "evidence_collection_template"
    ]
    assert path["diagnostic_branches"][0]["possible_cause"] == (
        "No verified technical cause recorded"
    )


def test_scenario_path_without_causal_evidence_never_invents_a_solution() -> None:
    scenario = {
        "service_function_id": "NEW_FEATURE",
        "service_function": "New Feature",
        "service_function_label": "New Feature",
        "scenario_id": "FS-0003",
        "customer_symptom": "The feature is unavailable",
        "trigger_condition": "After activation",
        "observed_behaviour": "The entry is disabled",
        "expected_behaviour": "The entry is enabled",
        "rep_title": "Feature unavailable",
        "ticket_ids": ["D-3"],
        "ticket_count": 1,
        "recovery_action": "",
        "evidence_confidence": "medium",
    }

    path = build_scenario_diagnosis_path(
        scenario=scenario,
        source_graphs=[],
        ticket_evidence_by_id={"D-3": {"rca_root_cause": ""}},
    )

    assert len(path["diagnostic_branches"]) == 1
    fallback = path["diagnostic_branches"][0]
    assert fallback["basis"] == "evidence_collection_template"
    assert fallback["possible_cause"] == "No verified technical cause recorded"
    assert fallback["resolution"] == ""
    assert fallback["resolution_status"] == "no_recorded_recovery"
    assert fallback["escalation"] == "Prepare an engineering evidence package"


def test_v12_loader_supports_legacy_ticket_mapping_sheet_name(tmp_path) -> None:
    workbook_path = tmp_path / "legacy-v12.xlsx"
    workbook = Workbook()
    scenario_sheet = workbook.active
    scenario_sheet.title = "01_Scenario_Library"
    scenario_sheet.append([
        "Service Function", "Scenario ID", "Problem Type", "Customer Symptom",
        "Trigger Condition", "Ticket Count", "Recorded Recovery Action",
        "Evidence Confidence", "Rep Ticket ID",
    ])
    scenario_sheet.append([
        "CarPlay", "FS-0001", "Connection / Pairing", "CarPlay cannot connect",
        "During connection", 1, "", "high", "D-1",
    ])
    mapping_sheet = workbook.create_sheet("06_Ticket_Mapping")
    mapping_sheet.append(["Ticket ID", "Scenario ID"])
    mapping_sheet.append(["D-1", "FS-0001"])
    workbook.save(workbook_path)
    knowledge_base_path = tmp_path / "knowledge-base.json"
    knowledge_base_path.write_text(
        json.dumps({
            "decision_trees": [
                {
                    "service_function_id": "CARPLAY",
                    "service_function": "CarPlay",
                    "service_function_label": "CarPlay",
                    "service_domain": "Phone Projection",
                    "catalog_status": "after_sales_reference",
                }
            ]
        }),
        encoding="utf-8",
    )

    scenarios, services = load_v12_scenario_paths(workbook_path, knowledge_base_path)

    assert services[0]["service_function_id"] == "CARPLAY"
    assert scenarios[0]["ticket_ids"] == ["D-1"]


def test_catalog_from_files_covers_data_only_and_source_backed_scenarios(tmp_path) -> None:
    scenario_workbook_path = tmp_path / "scenario-library.xlsx"
    workbook = Workbook()
    scenario_sheet = workbook.active
    scenario_sheet.title = "01_Scenario_Library"
    scenario_sheet.append([
        "Service Function", "Scenario ID", "Problem Type", "Customer Symptom",
        "Trigger Condition", "Ticket Count", "Rep Ticket ID", "Rep Title",
        "Observed Behaviour", "Expected Behaviour", "Recorded Recovery Action",
        "Evidence Confidence",
    ])
    scenario_sheet.append([
        "CarPlay", "FS-0001", "Connection / Pairing", "CarPlay cannot connect through USB",
        "After cable connection", 1, "D-1", "USB CarPlay failure",
        "No CarPlay recognition", "CarPlay starts", "", "high",
    ])
    scenario_sheet.append([
        "New Feature", "FS-0002", "Other", "New feature is unavailable",
        "After activation", 1, "D-2", "New feature unavailable",
        "Entry disabled", "Entry enabled", "Restart the application", "medium",
    ])
    mapping_sheet = workbook.create_sheet("07_Ticket_Mapping")
    mapping_sheet.append(["Ticket ID", "Scenario ID"])
    mapping_sheet.append(["D-1", "FS-0001"])
    mapping_sheet.append(["D-2", "FS-0002"])
    workbook.save(scenario_workbook_path)

    knowledge_base_path = tmp_path / "knowledge-base.json"
    knowledge_base_path.write_text(
        json.dumps({
            "decision_trees": [
                {
                    "service_function_id": "CARPLAY",
                    "service_function": "CarPlay",
                    "service_function_label": "CarPlay",
                    "service_domain": "Phone Projection",
                    "catalog_status": "after_sales_reference",
                },
                {
                    "service_function_id": "NEW_FEATURE",
                    "service_function": "New Feature",
                    "service_function_label": "New Feature",
                    "service_domain": "New Domain",
                    "catalog_status": "idcevo_function_map_feature",
                },
            ],
            "after_sales_reference_trees": [
                {
                    "reference_tree_id": "DTREF-CARPLAY",
                    "source_file": "carplay.png",
                    "source_asset": "decision_tree_sources/lossless_webp/carplay.webp",
                    "service_function_id": "CARPLAY",
                    "tree_version": "1.0",
                }
            ],
        }),
        encoding="utf-8",
    )
    source_workbook_path = tmp_path / "source.xlsx"
    source_workbook = Workbook()
    source_sheet = source_workbook.active
    source_sheet.title = "05_全部明细"
    source_sheet.append([
        "Ticket ID", "RCA Root Cause", "Assigned ECU", "Software Version",
    ])
    source_sheet.append(["D-1", "", "HU", "1.0"])
    source_sheet.append(["D-2", "Backend activation request timeout", "HU", "1.0"])
    source_workbook.save(source_workbook_path)

    registry_path = tmp_path / "tree_registry.json"
    registry_path.write_text(
        json.dumps({
            "trees": [
                {
                    "tree_id": "CARPLAY",
                    "tree_name_cn": "CarPlay",
                    "tree_name_en": "CarPlay",
                    "source_image": "carplay.png",
                }
            ]
        }),
        encoding="utf-8",
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps({
            "images": [
                {
                    "source_filename": "carplay.png",
                    "output_filename": "carplay.webp",
                    "width": 100,
                    "height": 200,
                    "output_sha256": "abc123",
                }
            ]
        }),
        encoding="utf-8",
    )
    extracted_directory = tmp_path / "extracted"
    extracted_directory.mkdir()
    (extracted_directory / "CARPLAY.json").write_text(
        json.dumps({
            "tree_id": "CARPLAY",
            "tree_name_cn": "CarPlay",
            "tree_name_en": "CarPlay",
            "source_image": "carplay.png",
            "nodes": [
                {"level": 1, "node_path": "CARPLAY", "is_leaf": False},
                {
                    "level": 2,
                    "node_path": "CARPLAY > USB connection failure",
                    "symptom_cn": "CarPlay cannot connect through USB",
                    "possible_cause_cn": "USB cable fault",
                    "check_step_cn": "Check the USB cable",
                    "solution_cn": "Replace the USB cable",
                    "escalation_path": "L1 to L3",
                    "is_leaf": True,
                },
            ],
        }),
        encoding="utf-8",
    )

    catalog = build_scenario_diagnosis_catalog_from_files(
        scenario_workbook_path=scenario_workbook_path,
        knowledge_base_path=knowledge_base_path,
        source_workbook_path=source_workbook_path,
        registry_path=registry_path,
        manifest_path=manifest_path,
        extracted_directory=extracted_directory,
    )

    assert catalog["summary"]["service_function_count"] == 2
    assert catalog["summary"]["scenario_path_count"] == 2
    carplay = next(
        item for item in catalog["functions"]
        if item["service_function_id"] == "CARPLAY"
    )
    new_feature = next(
        item for item in catalog["functions"]
        if item["service_function_id"] == "NEW_FEATURE"
    )
    assert carplay["scenario_paths"][0]["source_graph_count"] == 1
    assert any(
        branch["basis"] == "after_sales_experience_source_path"
        for branch in carplay["scenario_paths"][0]["diagnostic_branches"]
    )
    assert new_feature["source_graphs"] == []
    assert any(
        branch["basis"] == "ticket_recorded_rca_candidate"
        for branch in new_feature["scenario_paths"][0]["diagnostic_branches"]
    )

    html = render_scenario_diagnosis_catalog_html(catalog)
    assert "Scenario Diagnosis Path" in html
    assert "Customer Symptom -> Trigger -> Possible Cause -> Check Steps" in html
    assert "CarPlay cannot connect through USB" in html


def test_artifact_writer_emits_standalone_html_and_json(tmp_path) -> None:
    catalog = {
        "schema_version": "phase09-scenario-diagnosis-tree-catalog-v2",
        "summary": {"scenario_path_count": 0},
        "functions": [],
    }
    output_html = tmp_path / "catalog.html"
    output_json = tmp_path / "catalog.json"

    write_scenario_diagnosis_catalog_artifacts(
        catalog=catalog,
        output_html=output_html,
        output_json=output_json,
    )

    assert output_html.is_file()
    assert output_json.is_file()
    assert json.loads(output_json.read_text(encoding="utf-8")) == catalog
    assert "Scenario Diagnosis Path" in output_html.read_text(encoding="utf-8")


def test_builder_supports_direct_script_invocation() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    script_path = (
        repository_root
        / "backend"
        / "analytics"
        / "phase09_scenario_diagnosis_tree_catalog.py"
    )

    result = subprocess.run(
        [sys.executable, str(script_path), "--help"],
        cwd=repository_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "scenario-level after-sales diagnosis trees" in result.stdout