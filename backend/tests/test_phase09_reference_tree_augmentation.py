from backend.analytics.phase09_reference_tree_augmentation import (
    build_reference_tree_augmentation,
    build_reference_tree_augmentation_from_files,
    load_v12_scenario_records,
    validate_source_graph,
    write_reference_tree_augmentation_artifacts,
    validate_legacy_path_draft,
)


def test_reference_tree_overlay_keeps_unverified_drafts_out_of_branch_mapping() -> None:
    registry_trees = [
        {
            "tree_id": "CARPLAY",
            "tree_name_cn": "CarPlay",
            "tree_name_en": "CarPlay",
            "source_image": "carplay.png",
        }
    ]
    source_catalog = [
        {
            "reference_tree_id": "DTREF-CARPLAY",
            "source_file": "carplay.png",
            "source_asset": "decision_tree_sources/lossless_webp/carplay.webp",
            "service_function_id": "CARPLAY",
            "tree_version": "1.0",
        }
    ]
    legacy_drafts = {
        "CARPLAY": {
            "tree_id": "CARPLAY",
            "source_image": "carplay.webp",
            "nodes": [
                {
                    "node_path": "CARPLAY",
                    "is_leaf": True,
                },
                {
                    "node_path": "CARPLAY > Connection",
                    "is_leaf": True,
                },
            ],
        }
    }
    scenarios = [
        {
            "scenario_id": "FS-0001",
            "service_function_id": "CARPLAY",
            "problem_type": "Connection / Pairing",
            "customer_symptom": "CarPlay cannot connect",
            "trigger_condition": "During connection",
            "ticket_count": 3,
            "recorded_solution": "Reconnect the cable",
            "evidence_confidence": "high",
            "rep_ticket_id": "D-1",
        }
    ]

    draft_issues = validate_legacy_path_draft(legacy_drafts["CARPLAY"])
    overlay = build_reference_tree_augmentation(
        registry_trees=registry_trees,
        source_catalog=source_catalog,
        scenario_records=scenarios,
        legacy_drafts=legacy_drafts,
    )

    assert "nonterminal_nodes_marked_leaf" in draft_issues
    assert "graph_edges_not_captured" in draft_issues
    assert overlay["data_policy"]["source_tree_policy"].startswith(
        "Original after-sales source images"
    )

    reference_tree = overlay["reference_trees"][0]
    assert reference_tree["source_tree_authority"] == "after_sales_source_image"
    assert reference_tree["source_graph_status"] == "requires_human_verified_graph"
    assert reference_tree["legacy_path_draft_status"] == "invalid_for_graph_mapping"
    assert reference_tree["phase09_scenario_count"] == 1
    assert reference_tree["phase09_ticket_count"] == 3

    candidate = overlay["unmapped_scenario_candidates"][0]
    assert candidate["reference_tree_id"] == "DTREF-CARPLAY"
    assert candidate["reference_branch_id"] == ""
    assert candidate["mapping_status"] == "unmapped_pending_branch_verification"
    assert candidate["ticket_count"] == 3

    review_item = overlay["graph_structuring_review_queue"][0]
    assert review_item["tree_id"] == "CARPLAY"
    assert review_item["review_status"] == "requires_human_verified_graph"
    assert review_item["phase09_ticket_count"] == 3


def test_reference_tree_overlay_exports_source_image_and_unmapped_review_data(tmp_path) -> None:
    overlay = build_reference_tree_augmentation(
        registry_trees=[
            {
                "tree_id": "CARPLAY",
                "tree_name_cn": "CarPlay",
                "tree_name_en": "CarPlay",
                "source_image": "carplay.png",
            }
        ],
        source_catalog=[
            {
                "reference_tree_id": "DTREF-CARPLAY",
                "source_file": "carplay.png",
                "source_asset": "decision_tree_sources/lossless_webp/carplay.webp",
                "service_function_id": "CARPLAY",
                "tree_version": "1.0",
            }
        ],
        scenario_records=[
            {
                "scenario_id": "FS-0001",
                "service_function_id": "CARPLAY",
                "problem_type": "Connection / Pairing",
                "customer_symptom": "CarPlay cannot connect",
                "trigger_condition": "During connection",
                "ticket_count": 3,
                "recorded_solution": "Reconnect the cable",
                "evidence_confidence": "high",
                "rep_ticket_id": "D-1",
            },
            {
                "scenario_id": "FS-0002",
                "service_function_id": "HICAR",
                "problem_type": "Connection / Pairing",
                "customer_symptom": "HiCar cannot reconnect",
                "trigger_condition": "After vehicle wake-up",
                "ticket_count": 4,
                "recorded_solution": "",
                "evidence_confidence": "high",
                "rep_ticket_id": "D-2",
            }
        ],
        legacy_drafts={},
    )
    output_xlsx = tmp_path / "reference-tree-overlay.xlsx"
    output_json = tmp_path / "reference-tree-overlay.json"
    output_html = tmp_path / "reference-tree-overlay.html"

    write_reference_tree_augmentation_artifacts(
        overlay=overlay,
        output_xlsx=output_xlsx,
        output_json=output_json,
        output_html=output_html,
    )

    from openpyxl import load_workbook
    import json

    workbook = load_workbook(output_xlsx, read_only=True, data_only=True)
    try:
        assert workbook.sheetnames == [
            "00_Overview",
            "01_Reference_Tree_Catalog",
            "02_Phase09_Data_Support",
            "03_Unmapped_Scenario_Review",
            "04_Uncovered_Service_Gaps",
            "05_Graph_Structuring_Review",
            "06_Data_Dictionary",
        ]
        candidate_headers = next(
            workbook["03_Unmapped_Scenario_Review"].iter_rows(
                min_row=1, max_row=1, values_only=True
            )
        )
        candidate_row = next(
            workbook["03_Unmapped_Scenario_Review"].iter_rows(
                min_row=2, max_row=2, values_only=True
            )
        )
        candidate_columns = {
            header: index for index, header in enumerate(candidate_headers)
        }
        assert candidate_row[candidate_columns["Reference Branch ID"]] in ("", None)
        assert candidate_row[candidate_columns["Mapping Status"]] == (
            "unmapped_pending_branch_verification"
        )
        uncovered_headers = next(
            workbook["04_Uncovered_Service_Gaps"].iter_rows(
                min_row=1, max_row=1, values_only=True
            )
        )
        uncovered_row = next(
            workbook["04_Uncovered_Service_Gaps"].iter_rows(
                min_row=2, max_row=2, values_only=True
            )
        )
        uncovered_columns = {
            header: index for index, header in enumerate(uncovered_headers)
        }
        assert uncovered_row[uncovered_columns["Service Function ID"]] == "HICAR"
        assert uncovered_row[uncovered_columns["Coverage Status"]] == (
            "no_matching_after_sales_reference_tree"
        )
    finally:
        workbook.close()

    payload = json.loads(output_json.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "phase09-reference-tree-augmentation-v1"
    assert payload["unmapped_scenario_candidates"][0]["reference_branch_id"] == ""

    html = output_html.read_text(encoding="utf-8")
    assert "After-Sales Reference Tree Evidence Overlay" in html
    assert "decision_tree_sources/lossless_webp/carplay.webp" in html
    assert "Original after-sales source tree" in html
    assert "unmapped_pending_branch_verification" in html
    assert "Uncovered service-function candidates" in html
    assert "no_matching_after_sales_reference_tree" in html


def test_reference_tree_overlay_keeps_services_without_a_source_tree_in_coverage_gaps() -> None:
    overlay = build_reference_tree_augmentation(
        registry_trees=[
            {
                "tree_id": "CARPLAY",
                "tree_name_cn": "CarPlay",
                "tree_name_en": "CarPlay",
                "source_image": "carplay.png",
            }
        ],
        source_catalog=[
            {
                "reference_tree_id": "DTREF-CARPLAY",
                "source_file": "carplay.png",
                "source_asset": "decision_tree_sources/lossless_webp/carplay.webp",
                "service_function_id": "CARPLAY",
                "tree_version": "1.0",
            }
        ],
        scenario_records=[
            {
                "scenario_id": "FS-0002",
                "service_function_id": "HICAR",
                "problem_type": "Connection / Pairing",
                "customer_symptom": "HiCar cannot reconnect",
                "trigger_condition": "After vehicle wake-up",
                "ticket_count": 4,
                "recorded_solution": "",
                "evidence_confidence": "high",
                "rep_ticket_id": "D-2",
            }
        ],
        legacy_drafts={},
    )

    assert overlay["uncovered_service_function_candidates"] == [
        {
            "service_function_id": "HICAR",
            "scenario_id": "FS-0002",
            "problem_type": "Connection / Pairing",
            "customer_symptom": "HiCar cannot reconnect",
            "trigger_condition": "After vehicle wake-up",
            "ticket_count": 4,
            "recorded_solution": "",
            "evidence_confidence": "high",
            "rep_ticket_id": "D-2",
            "coverage_status": "no_matching_after_sales_reference_tree",
        }
    ]


def test_load_v12_scenario_records_resolves_service_function_id(tmp_path) -> None:
    from openpyxl import Workbook
    import json

    workbook_path = tmp_path / "scenario-library.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "01_Scenario_Library"
    worksheet.append([
        "Service Function", "Scenario ID", "Problem Type", "Customer Symptom",
        "Trigger Condition", "Ticket Count", "Recorded Recovery Action",
        "Evidence Confidence", "Rep Ticket ID",
    ])
    worksheet.append([
        "CarPlay", "FS-0001", "Connection / Pairing", "CarPlay cannot connect",
        "During connection", 3, "Reconnect the cable", "high", "D-1",
    ])
    workbook.save(workbook_path)

    knowledge_base_path = tmp_path / "knowledge-base.json"
    knowledge_base_path.write_text(
        json.dumps({
            "decision_trees": [
                {"service_function": "CarPlay", "service_function_id": "CARPLAY"}
            ]
        }),
        encoding="utf-8",
    )

    records = load_v12_scenario_records(
        scenario_workbook_path=workbook_path,
        knowledge_base_path=knowledge_base_path,
    )

    assert records == [
        {
            "service_function_id": "CARPLAY",
            "scenario_id": "FS-0001",
            "problem_type": "Connection / Pairing",
            "customer_symptom": "CarPlay cannot connect",
            "trigger_condition": "During connection",
            "ticket_count": 3,
            "recorded_solution": "Reconnect the cable",
            "evidence_confidence": "high",
            "rep_ticket_id": "D-1",
        }
    ]


def test_load_v12_scenario_records_rejects_inconsistent_snapshot(tmp_path) -> None:
    from openpyxl import Workbook
    import json
    import pytest

    workbook_path = tmp_path / "scenario-library.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "01_Scenario_Library"
    worksheet.append([
        "Service Function", "Scenario ID", "Problem Type", "Customer Symptom",
        "Trigger Condition", "Ticket Count", "Recorded Recovery Action",
        "Evidence Confidence", "Rep Ticket ID",
    ])
    worksheet.append([
        "CarPlay", "FS-0001", "Connection / Pairing", "CarPlay cannot connect",
        "During connection", 3, "Reconnect the cable", "high", "D-1",
    ])
    workbook.save(workbook_path)
    knowledge_base_path = tmp_path / "knowledge-base.json"
    knowledge_base_path.write_text(
        json.dumps({
            "decision_trees": [
                {
                    "service_function": "CarPlay",
                    "service_function_id": "CARPLAY",
                    "scenario_count": 0,
                }
            ]
        }),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="scenario count mismatch"):
        load_v12_scenario_records(
            scenario_workbook_path=workbook_path,
            knowledge_base_path=knowledge_base_path,
        )


def test_build_reference_tree_augmentation_from_files_loads_all_input_layers(tmp_path) -> None:
    from openpyxl import Workbook
    import json

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
    knowledge_base_path = tmp_path / "knowledge-base.json"
    knowledge_base_path.write_text(
        json.dumps({
            "decision_trees": [
                {"service_function": "CarPlay", "service_function_id": "CARPLAY"}
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
    workbook_path = tmp_path / "scenario-library.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "01_Scenario_Library"
    worksheet.append([
        "Service Function", "Scenario ID", "Problem Type", "Customer Symptom",
        "Trigger Condition", "Ticket Count", "Recorded Recovery Action",
        "Evidence Confidence", "Rep Ticket ID",
    ])
    worksheet.append([
        "CarPlay", "FS-0001", "Connection / Pairing", "CarPlay cannot connect",
        "During connection", 3, "Reconnect the cable", "high", "D-1",
    ])
    workbook.save(workbook_path)
    draft_directory = tmp_path / "extracted"
    draft_directory.mkdir()
    (draft_directory / "CARPLAY.json").write_text(
        json.dumps({"tree_id": "CARPLAY", "nodes": []}), encoding="utf-8"
    )

    overlay = build_reference_tree_augmentation_from_files(
        registry_path=registry_path,
        scenario_workbook_path=workbook_path,
        knowledge_base_path=knowledge_base_path,
        legacy_draft_directory=draft_directory,
    )

    assert overlay["reference_trees"][0]["reference_tree_id"] == "DTREF-CARPLAY"
    assert overlay["reference_trees"][0]["phase09_ticket_count"] == 3
    assert overlay["reference_trees"][0]["legacy_path_draft_status"] == (
        "invalid_for_graph_mapping"
    )
    assert overlay["input_provenance"] == {
        "registry_path": str(registry_path),
        "scenario_workbook_path": str(workbook_path),
        "knowledge_base_path": str(knowledge_base_path),
        "legacy_draft_directory": str(draft_directory),
        "scenario_record_count": 1,
    }


def test_source_graph_validation_requires_ids_bounds_and_directed_edges() -> None:
    valid_graph = {
        "tree_id": "CARPLAY",
        "graph_status": "human_verified",
        "nodes": [
            {
                "node_id": "C1",
                "node_type": "decision",
                "source_text": "Feature available?",
                "source_bounds": {"x": 10, "y": 20, "width": 30, "height": 40},
                "review_status": "human_verified",
            },
            {
                "node_id": "R1",
                "node_type": "resolution",
                "source_text": "Resolve",
                "source_bounds": {"x": 60, "y": 20, "width": 30, "height": 40},
                "review_status": "human_verified",
            },
        ],
        "edges": [
            {
                "edge_id": "E-C1-R1-TRUE",
                "from_node_id": "C1",
                "to_node_id": "R1",
                "branch_outcome": "TRUE",
                "review_status": "human_verified",
            }
        ],
    }
    invalid_graph = {
        "tree_id": "CARPLAY",
        "nodes": [{"node_id": "C1", "source_text": "Feature available?"}],
        "edges": [{"from_node_id": "C1", "to_node_id": "Missing"}],
    }

    assert validate_source_graph(valid_graph) == []
    assert set(validate_source_graph(invalid_graph)) >= {
        "node_source_bounds_missing",
        "edge_id_missing",
        "edge_branch_outcome_invalid",
        "edge_target_not_found",
    }