import json

from openpyxl import Workbook

from backend.analytics.phase09_all_function_after_sales_catalog import (
    build_ai_reviewed_source_graph,
    build_all_function_catalog_from_files,
    compact_v1_pattern_label,
    render_all_function_catalog_html,
    write_all_function_catalog_artifacts,
)


def test_ai_reviewed_source_graph_rebuilds_one_based_path_hierarchy() -> None:
    draft = {
        "tree_id": "NAV",
        "tree_name_cn": "Navigation",
        "tree_name_en": "Navigation",
        "source_image": "Navigation.png",
        "nodes": [
            {
                "level": 1,
                "node_path": "NAV",
                "symptom_cn": "",
                "is_leaf": False,
                "confidence": "high",
            },
            {
                "level": 2,
                "node_path": "NAV > Location issue",
                "symptom_cn": "Location does not update",
                "is_leaf": False,
                "confidence": "high",
            },
            {
                "level": 3,
                "node_path": "NAV > Location issue > No GPS signal",
                "symptom_cn": "No GPS signal",
                "possible_cause_cn": "GPS antenna fault",
                "check_step_cn": "Check antenna connection",
                "solution_cn": "Repair the antenna connection",
                "escalation_path": "L1 to L3",
                "is_leaf": True,
                "confidence": "medium",
            },
        ],
    }

    graph = build_ai_reviewed_source_graph(draft)

    assert graph["graph_status"] == "ai_structured_reviewed"
    assert graph["structural_validation_status"] == "pass"
    assert graph["source_path_node_count"] == 3
    assert len(graph["nodes"]) == 3
    assert len(graph["edges"]) == 2
    root = next(node for node in graph["nodes"] if node["node_id"] == "ROOT")
    assert root["path_depth"] == 0
    terminal = next(node for node in graph["nodes"] if node["is_terminal"])
    assert terminal["source_text"] == "No GPS signal"
    assert terminal["source_level"] == 3
    assert {edge["branch_outcome"] for edge in graph["edges"]} == {"NEXT"}
    assert all(edge["edge_provenance"] == "path_hierarchy_inference" for edge in graph["edges"])


def test_catalog_keeps_image_backed_and_data_only_functions(tmp_path) -> None:
    workbook_path = tmp_path / "v12.xlsx"
    workbook = Workbook()
    catalog_sheet = workbook.active
    catalog_sheet.title = "03_Service_Function_Catalog"
    catalog_sheet.append([
        "Service Function", "Catalog Status", "Scenario Count", "Ticket Count",
        "Source Ticket Mentions", "Phase 09 Evidence Status", "Problem Types",
        "Diagnostic Layers", "Solution Source Policy", "Node Count", "Edge Count",
        "Tree Generation", "Source Status",
    ])
    catalog_sheet.append([
        "Navigation", "after_sales_reference", 1, 2, 3,
        "field_scenario_data_available", "Navigation / Location", "Issue Group",
        "Recorded evidence only", 5, 4, "field_scenario_symptom_solution_v1",
        "data-derived",
    ])
    catalog_sheet.append([
        "New Function", "idcevo_function_map_feature", 1, 1, 1,
        "field_scenario_data_available", "Other", "Issue Group",
        "Recorded evidence only", 5, 4, "field_scenario_symptom_solution_v1",
        "data-derived",
    ])
    nodes_sheet = workbook.create_sheet("05_Decision_Tree_Nodes")
    nodes_sheet.append([
        "Service Function", "Node ID", "Parent Node ID", "Node Type", "Data Key",
        "Question / Solution", "Evidence Source", "Source Scenario Count",
        "Source Ticket Count", "Source Scenario IDs", "Solution Status", "Node Source",
    ])
    nodes_sheet.append([
        "Navigation", "START", "", "Entry", "NAVIGATION", "Navigation", "", 1,
        2, "FS-0001", "", "field_scenario_symptom_solution",
    ])
    nodes_sheet.append([
        "Navigation", "ISS-01", "START", "Issue Group", "navigation_location",
        "Navigation / Location", "Customer Symptom", 1, 2, "FS-0001", "",
        "field_scenario_symptom_solution",
    ])
    nodes_sheet.append([
        "New Function", "START", "", "Entry", "NEW_FUNCTION", "New Function", "", 1,
        1, "FS-0002", "", "field_scenario_symptom_solution",
    ])
    scenario_sheet = workbook.create_sheet("01_Scenario_Library")
    scenario_sheet.append([
        "Service Function", "Scenario ID", "Problem Type", "Customer Symptom",
        "Trigger Condition", "Ticket Count", "Rep Ticket ID", "Recorded Recovery Action",
        "Evidence Confidence",
    ])
    scenario_sheet.append([
        "Navigation", "FS-0001", "Navigation / Location", "Route unavailable",
        "During route planning", 2, "D-1", "Restart navigation", "high",
    ])
    scenario_sheet.append([
        "New Function", "FS-0002", "Other", "Feature unavailable",
        "After activation", 1, "D-2", "", "medium",
    ])
    workbook.save(workbook_path)

    knowledge_base_path = tmp_path / "knowledge-base.json"
    knowledge_base_path.write_text(
        json.dumps({
            "decision_trees": [
                {
                    "tree_id": "DT-NAVIGATION",
                    "service_function_id": "NAVIGATION",
                    "service_function": "Navigation",
                    "service_function_label": "Navigation",
                    "service_domain": "Navigation / Location",
                    "catalog_status": "after_sales_reference",
                    "reference_tree_ids": ["DTREF-NAVIGATION"],
                },
                {
                    "tree_id": "DT-NEW_FUNCTION",
                    "service_function_id": "NEW_FUNCTION",
                    "service_function": "New Function",
                    "service_function_label": "New Function",
                    "service_domain": "New Domain",
                    "catalog_status": "idcevo_function_map_feature",
                    "reference_tree_ids": [],
                },
            ],
            "after_sales_reference_trees": [
                {
                    "reference_tree_id": "DTREF-NAVIGATION",
                    "source_file": "Navigation.png",
                    "source_asset": "decision_tree_sources/lossless_webp/Navigation.webp",
                    "service_function_id": "NAVIGATION",
                    "tree_version": "1.0",
                }
            ],
        }),
        encoding="utf-8",
    )
    registry_path = tmp_path / "tree_registry.json"
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
    extracted_directory = tmp_path / "extracted"
    extracted_directory.mkdir()
    (extracted_directory / "NAV.json").write_text(
        json.dumps({
            "tree_id": "NAV",
            "tree_name_cn": "Navigation",
            "tree_name_en": "Navigation",
            "source_image": "Navigation.png",
            "nodes": [
                {"level": 1, "node_path": "NAV", "is_leaf": False, "confidence": "high"},
                {
                    "level": 2,
                    "node_path": "NAV > Route unavailable",
                    "symptom_cn": "Route unavailable",
                    "solution_cn": "Restart navigation",
                    "is_leaf": True,
                    "confidence": "high",
                },
            ],
        }),
        encoding="utf-8",
    )

    catalog = build_all_function_catalog_from_files(
        scenario_workbook_path=workbook_path,
        knowledge_base_path=knowledge_base_path,
        registry_path=registry_path,
        manifest_path=manifest_path,
        extracted_directory=extracted_directory,
    )

    assert catalog["summary"]["service_function_count"] == 2
    assert catalog["summary"]["image_backed_function_count"] == 1
    navigation = next(
        tree for tree in catalog["functions"]
        if tree["service_function_id"] == "NAVIGATION"
    )
    new_function = next(
        tree for tree in catalog["functions"]
        if tree["service_function_id"] == "NEW_FUNCTION"
    )
    assert navigation["source_graphs"][0]["graph_status"] == "ai_structured_reviewed"
    assert navigation["source_graphs"][0]["source_asset"].endswith("Navigation.webp")
    assert navigation["data_tree_nodes"][1]["parent_node_id"] == "START"
    assert new_function["source_graphs"] == []
    assert new_function["scenario_records"][0]["scenario_id"] == "FS-0002"

    html = render_all_function_catalog_html(catalog)
    assert "All-Function After-Sales Decision Tree Catalog" in html
    assert "AI structured review" in html
    assert "New Function" in html
    assert "Navigation.webp" in html


def test_catalog_supports_current_v12_node_sheet_name(tmp_path) -> None:
    workbook_path = tmp_path / "v12-current.xlsx"
    workbook = Workbook()
    catalog_sheet = workbook.active
    catalog_sheet.title = "03_Service_Function_Catalog"
    catalog_sheet.append([
        "Service Function", "Catalog Status", "Scenario Count", "Ticket Count",
        "Source Ticket Mentions", "Phase 09 Evidence Status", "Problem Types",
        "Diagnostic Layers", "Solution Source Policy", "Node Count", "Edge Count",
        "Tree Generation", "Source Status",
    ])
    catalog_sheet.append([
        "Navigation", "after_sales_reference", 1, 2, 2,
        "field_scenario_data_available", "Navigation / Location", "Issue Group",
        "Recorded evidence only", 2, 1, "field_scenario_symptom_solution_v1",
        "data-derived",
    ])
    nodes_sheet = workbook.create_sheet("04_Decision_Tree_Nodes")
    nodes_sheet.append([
        "Service Function", "Node ID", "Parent Node ID", "Node Type", "Data Key",
        "Question / Solution", "Evidence Source", "Source Scenario Count",
        "Source Ticket Count", "Source Scenario IDs", "Solution Status", "Node Source",
    ])
    nodes_sheet.append([
        "Navigation", "START", "", "Entry", "NAVIGATION", "Navigation", "", 1,
        2, "FS-0001", "", "field_scenario_symptom_solution",
    ])
    nodes_sheet.append([
        "Navigation", "SYM-01", "START", "Symptom Pattern", "connection-lost",
        "Observed symptom patterns: Connection lost (2 tickets)", "Customer Symptom", 1,
        2, "FS-0001", "", "field_scenario_symptom_solution",
    ])
    scenario_sheet = workbook.create_sheet("01_Scenario_Library")
    scenario_sheet.append([
        "Service Function", "Scenario ID", "Problem Type", "Customer Symptom",
        "Trigger Condition", "Ticket Count", "Rep Ticket ID", "Recorded Recovery Action",
        "Evidence Confidence",
    ])
    scenario_sheet.append([
        "Navigation", "FS-0001", "Navigation / Location", "Route unavailable",
        "During route planning", 2, "D-1", "", "high",
    ])
    workbook.save(workbook_path)

    knowledge_base_path = tmp_path / "knowledge-base.json"
    knowledge_base_path.write_text(
        json.dumps({
            "decision_trees": [{
                "tree_id": "DT-NAVIGATION",
                "service_function_id": "NAVIGATION",
                "service_function": "Navigation",
                "service_function_label": "Navigation",
                "service_domain": "Navigation / Location",
                "catalog_status": "after_sales_reference",
                "reference_tree_ids": [],
            }],
            "after_sales_reference_trees": [],
        }),
        encoding="utf-8",
    )
    registry_path = tmp_path / "tree_registry.json"
    registry_path.write_text(json.dumps({"trees": []}), encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"images": []}), encoding="utf-8")
    extracted_directory = tmp_path / "extracted"
    extracted_directory.mkdir()

    catalog = build_all_function_catalog_from_files(
        scenario_workbook_path=workbook_path,
        knowledge_base_path=knowledge_base_path,
        registry_path=registry_path,
        manifest_path=manifest_path,
        extracted_directory=extracted_directory,
    )

    navigation = catalog["functions"][0]
    assert navigation["data_tree_nodes"][1]["visual_label"] == "Connection lost (2)"


def test_artifact_writer_emits_html_and_json(tmp_path) -> None:
    catalog = {
        "schema_version": "phase09-all-function-after-sales-catalog-v1",
        "summary": {"service_function_count": 0},
        "functions": [],
    }
    output_html = tmp_path / "catalog.html"
    output_json = tmp_path / "catalog.json"

    write_all_function_catalog_artifacts(
        catalog=catalog,
        output_html=output_html,
        output_json=output_json,
    )

    assert output_html.is_file()
    assert output_json.is_file()
    assert json.loads(output_json.read_text(encoding="utf-8")) == catalog
    assert "All-Function After-Sales Decision Tree Catalog" in output_html.read_text(
        encoding="utf-8"
    )


def test_rendered_html_constrains_wide_tree_canvas_within_mobile_workspace() -> None:
    html = render_all_function_catalog_html({
        "schema_version": "phase09-all-function-after-sales-catalog-v1",
        "summary": {},
        "functions": [],
    })

    assert ".workspace { min-width: 0;" in html
    assert ".diagram-panel, .detail-panel, .table-shell { min-width: 0;" in html


def test_compact_v1_pattern_label_keeps_specific_ranked_patterns_only() -> None:
    symptom_label = compact_v1_pattern_label(
        "Symptom Pattern",
        "Observed symptom patterns: Connection lost (16 tickets); "
        "Other observed symptom pattern (11 tickets); unavailable (3 tickets); "
        "Other observed variants (10 groups)",
    )
    trigger_label = compact_v1_pattern_label(
        "Trigger Pattern",
        "Observed trigger patterns: Other reported trigger (20 tickets); "
        "While driving (19 tickets); after reboot (4 tickets); "
        "Other observed variants (3 groups)",
    )

    assert symptom_label == "Connection lost (16) · Unavailable (3)"
    assert trigger_label == "While driving (19) · After reboot (4)"
    assert "Observed" not in symptom_label
    assert "Other observed" not in symptom_label
    assert "Other reported" not in trigger_label


def test_v1_svg_labels_do_not_intercept_node_clicks() -> None:
    html = render_all_function_catalog_html({
        "schema_version": "phase09-all-function-after-sales-catalog-v1",
        "summary": {},
        "functions": [],
    })

    assert '"pointer-events": "none"' in html