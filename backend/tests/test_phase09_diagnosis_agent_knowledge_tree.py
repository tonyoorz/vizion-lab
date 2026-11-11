from openpyxl import load_workbook

from backend.analytics.phase09_diagnosis_agent_knowledge_tree import (
    build_feature_scaffold,
    build_growth_candidates,
    build_diagnosis_agent_workbook_from_files,
    write_diagnosis_agent_workbook,
)
import json
from openpyxl import Workbook


def test_feature_scaffold_keeps_ai_travel_companion_rules_unapproved() -> None:
    feature = {
        "feature_id": "AI_TRAVEL_COMPANION",
        "feature_name": "AI Empowered Travel Companion",
        "service_domain": "Navigation / Location",
    }

    nodes, edges = build_feature_scaffold(feature)

    node_ids = {node["node_id"] for node in nodes}
    assert {
        "ROOT",
        "C01_CONTEXT_CAPTURED",
        "C02_PRECONDITIONS_MET",
        "C05_SYMPTOM_CLASSIFIED",
        "C06_APPROVED_BRANCH_EXISTS",
        "A04_PROPOSE_BRANCH_FOR_REVIEW",
        "E01_ENGINEERING_ESCALATION",
    }.issubset(node_ids)
    assert all(node["rule_status"] == "template_unapproved" for node in nodes)
    assert all(node["execution_status"] == "not_executable" for node in nodes)
    assert all(edge["from_node_id"] in node_ids for edge in edges)
    assert all(edge["to_node_id"] in node_ids for edge in edges)
    assert any(
        edge["from_node_id"] == "C06_APPROVED_BRANCH_EXISTS"
        and edge["branch_outcome"] == "FALSE"
        and edge["to_node_id"] == "A04_PROPOSE_BRANCH_FOR_REVIEW"
        for edge in edges
    )


def test_direct_history_creates_review_candidate_without_promoting_rule() -> None:
    scenarios = [
        {
            "service_function_id": "AI_TRAVEL_COMPANION",
            "scenario_id": "FS-0001",
            "problem_type": "Navigation / Location",
            "customer_symptom": "Trip planning result does not appear",
            "trigger_condition": "After the driver starts a route search",
            "ticket_count": 2,
            "recorded_recovery_action": "Restart the application",
            "evidence_confidence": "high",
            "rep_ticket_id": "D-1",
        },
        {
            "service_function_id": "NAVIGATION",
            "scenario_id": "FS-0002",
            "problem_type": "Navigation / Location",
            "customer_symptom": "Route display is stale",
            "trigger_condition": "After a map update",
            "ticket_count": 4,
            "recorded_recovery_action": "",
            "evidence_confidence": "medium",
            "rep_ticket_id": "D-2",
        },
    ]

    candidates = build_growth_candidates("AI_TRAVEL_COMPANION", scenarios)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["scenario_id"] == "FS-0001"
    assert candidate["parent_node_id"] == "C05_SYMPTOM_CLASSIFIED"
    assert candidate["candidate_status"] == "evidence_candidate_needs_review"
    assert candidate["rule_status"] == "not_an_approved_rule"
    assert candidate["recovery_action_status"] == "recorded_evidence_not_rule"


def test_workbook_separates_scaffold_evidence_and_approval_queue(tmp_path) -> None:
    feature = {
        "feature_id": "AI_TRAVEL_COMPANION",
        "feature_name": "AI Empowered Travel Companion",
        "service_domain": "Navigation / Location",
    }
    nodes, edges = build_feature_scaffold(feature)
    scenarios = [
        {
            "service_function_id": "AI_TRAVEL_COMPANION",
            "scenario_id": "FS-0001",
            "problem_type": "Navigation / Location",
            "customer_symptom": "Trip planning result does not appear",
            "trigger_condition": "After the driver starts a route search",
            "ticket_count": 2,
            "recorded_recovery_action": "Restart the application",
            "evidence_confidence": "high",
            "rep_ticket_id": "D-1",
        }
    ]
    candidates = build_growth_candidates(feature["feature_id"], scenarios)
    output_path = tmp_path / "diagnosis-agent.xlsx"

    write_diagnosis_agent_workbook(
        output_path=output_path,
        feature=feature,
        scaffold_nodes=nodes,
        scaffold_edges=edges,
        source_assets=[],
        scenario_records=scenarios,
        ticket_evidence=[],
        growth_candidates=candidates,
        readiness_metrics={
            "reference_asset_count": 39,
            "structured_graph_count": 0,
            "direct_scenario_count": 1,
            "direct_ticket_count": 2,
            "source_ticket_mention_count": 1,
        },
    )

    workbook = load_workbook(output_path, read_only=True, data_only=True)
    try:
        assert workbook.sheetnames == [
            "00_Architecture",
            "01_Feature_Contract",
            "02_Template_Nodes",
            "03_Template_Edges",
            "04_Source_Assets",
            "05_History_Scenarios",
            "06_Ticket_Evidence",
            "07_Growth_Candidates",
            "08_Approval_Queue",
            "09_Coverage_Gaps",
            "10_Data_Dictionary",
        ]
        candidate_headers = next(
            workbook["07_Growth_Candidates"].iter_rows(
                min_row=1, max_row=1, values_only=True
            )
        )
        candidate_row = next(
            workbook["07_Growth_Candidates"].iter_rows(
                min_row=2, max_row=2, values_only=True
            )
        )
        candidate_columns = {
            header: index for index, header in enumerate(candidate_headers)
        }
        assert candidate_row[candidate_columns["Rule Status"]] == (
            "not_an_approved_rule"
        )
        approval_headers = next(
            workbook["08_Approval_Queue"].iter_rows(
                min_row=1, max_row=1, values_only=True
            )
        )
        assert "Approval Decision" in approval_headers
    finally:
        workbook.close()


def test_file_builder_keeps_direct_history_separate_from_alias_mentions(tmp_path) -> None:
    feature = {
        "feature_id": "AI_TRAVEL_COMPANION",
        "feature_name": "AI Empowered Travel Companion",
        "service_domain": "Navigation / Location",
        "aliases": ["ai empowered travel companion", "travel companion"],
        "reference_tree_ids": ["NAV", "IPA"],
    }
    scenario_workbook_path = tmp_path / "scenario-library.xlsx"
    scenario_workbook = Workbook()
    scenario_sheet = scenario_workbook.active
    scenario_sheet.title = "01_Scenario_Library"
    scenario_sheet.append([
        "Service Function", "Scenario ID", "Problem Type", "Customer Symptom",
        "Trigger Condition", "Ticket Count", "Recorded Recovery Action",
        "Evidence Confidence", "Rep Ticket ID",
    ])
    scenario_sheet.append([
        "AI Empowered Travel Companion", "FS-0001", "Navigation / Location",
        "Trip planning result does not appear", "After a route search", 2,
        "Restart the application", "high", "D-1",
    ])
    scenario_sheet.append([
        "Navigation", "FS-0002", "Navigation / Location", "Map does not refresh",
        "After a map update", 4, "", "medium", "D-2",
    ])
    ticket_mapping_sheet = scenario_workbook.create_sheet("07_Ticket_Mapping")
    ticket_mapping_sheet.append([
        "Ticket ID", "Scenario ID", "Service Function ID",
    ])
    ticket_mapping_sheet.append(["D-1", "FS-0001", "AI_TRAVEL_COMPANION"])
    scenario_workbook.save(scenario_workbook_path)

    knowledge_base_path = tmp_path / "knowledge-base.json"
    knowledge_base_path.write_text(
        json.dumps({
            "decision_trees": [
                {
                    "service_function": "AI Empowered Travel Companion",
                    "service_function_id": "AI_TRAVEL_COMPANION",
                },
                {"service_function": "Navigation", "service_function_id": "NAVIGATION"},
            ]
        }),
        encoding="utf-8",
    )
    source_workbook_path = tmp_path / "source.xlsx"
    source_workbook = Workbook()
    source_sheet = source_workbook.active
    source_sheet.title = "05_All_Tickets"
    source_sheet.append(["Ticket ID", "Function", "Ticket Title", "Description Excerpt"])
    source_sheet.append([
        "D-1", "Navigation", "Travel companion route search fails",
        "AI Empowered Travel Companion has no result.",
    ])
    source_sheet.append([
        "D-2", "Navigation", "Map is stale", "No feature alias here.",
    ])
    source_workbook.save(source_workbook_path)

    registry_path = tmp_path / "tree-registry.json"
    registry_path.write_text(
        json.dumps({
            "trees": [
                {
                    "tree_id": "NAV",
                    "tree_name_cn": "Navigation",
                    "tree_name_en": "Navigation",
                    "source_image": "Navigation.png",
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
                    "source_filename": "Navigation.png",
                    "output_filename": "Navigation.webp",
                    "width": 100,
                    "height": 200,
                    "output_sha256": "abc123",
                }
            ]
        }),
        encoding="utf-8",
    )
    output_path = tmp_path / "diagnosis-agent.xlsx"

    metrics = build_diagnosis_agent_workbook_from_files(
        feature=feature,
        scenario_workbook_path=scenario_workbook_path,
        knowledge_base_path=knowledge_base_path,
        source_workbook_path=source_workbook_path,
        registry_path=registry_path,
        manifest_path=manifest_path,
        output_path=output_path,
    )

    assert metrics["direct_scenario_count"] == 1
    assert metrics["direct_ticket_count"] == 2
    assert metrics["source_ticket_mention_count"] == 1
    workbook = load_workbook(output_path, read_only=True, data_only=True)
    try:
        history_rows = list(
            workbook["05_History_Scenarios"].iter_rows(min_row=2, values_only=True)
        )
        assert len(history_rows) == 1
        assert history_rows[0][1] == "FS-0001"
        evidence_rows = list(
            workbook["06_Ticket_Evidence"].iter_rows(min_row=2, values_only=True)
        )
        assert len(evidence_rows) == 1
        assert evidence_rows[0][0] == "D-1"
        assert evidence_rows[0][4] == "FS-0001"
        assert evidence_rows[0][5] == "direct_feature_field_ticket_evidence"
        asset_rows = list(
            workbook["04_Source_Assets"].iter_rows(min_row=2, values_only=True)
        )
        assert asset_rows[0][0] == "NAV"
        assert asset_rows[0][7] == "requires_human_verified_graph"
    finally:
        workbook.close()