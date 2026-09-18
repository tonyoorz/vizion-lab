import importlib
import json
from pathlib import Path
import subprocess
import sys

import openpyxl
import pytest


def _load_builder_module():
    try:
        return importlib.import_module(
            "backend.analytics.phase09_failure_pattern_graph_build"
        )
    except ModuleNotFoundError:
        return None


def test_failure_pattern_graph_keeps_recorded_rca_as_review_required_candidate() -> None:
    builder = _load_builder_module()

    assert builder is not None, "V13 failure-pattern graph builder must be available"

    graph = builder.build_failure_pattern_graph(
        [
            {
                "service_function_id": "CARPLAY",
                "service_function": "CarPlay",
                "scenario_id": "FS-0001",
                "problem_type_id": "connection_pairing",
                "problem_type": "Connection / Pairing (连接/配对)",
                "customer_symptom": "CarPlay cannot connect to the phone",
                "trigger_condition": "After connecting the phone through USB",
                "ticket_ids": ["D-1", "D-2"],
                "ticket_count": 2,
                "recovery_action": "Reconnect the phone",
                "evidence_confidence": "high",
            }
        ],
        {
            "D-1": {"rca_root_cause": "Bluetooth pairing session timeout"},
            "D-2": {"rca_root_cause": ""},
        },
    )

    pattern = graph["failure_patterns"][0]
    assert pattern["service_function"] == "CarPlay"
    assert pattern["scenario_id"] == "FS-0001"
    assert pattern["failure_mode_candidate"] == "Bluetooth pairing session timeout"
    assert pattern["failure_mode_status"] == "recorded_candidate_review_required"
    assert pattern["source_ticket_count"] == 2
    assert pattern["causal_evidence_ticket_count"] == 1
    assert pattern["candidate_support_ratio"] == 0.5
    assert pattern["cause_probability_status"] == "not_estimated"
    assert pattern["recorded_recovery_action"] == "Reconnect the phone"
    assert {
        "Service Function",
        "Scenario",
        "Failure Pattern",
        "Recorded Failure Mode Candidate",
        "Recorded Recovery Action",
    }.issubset({node["node_type"] for node in graph["nodes"]})


def test_failure_pattern_graph_workbook_exports_reviewable_graph_sheets(tmp_path) -> None:
    builder = _load_builder_module()

    assert builder is not None
    write_workbook = getattr(builder, "write_failure_pattern_graph_workbook", None)
    assert callable(write_workbook), "V13 graph workbook writer must be available"
    graph = builder.build_failure_pattern_graph(
        [
            {
                "service_function_id": "CARPLAY",
                "service_function": "CarPlay",
                "scenario_id": "FS-0001",
                "problem_type": "Connection / Pairing (连接/配对)",
                "customer_symptom": "CarPlay cannot connect to the phone",
                "trigger_condition": "After connecting the phone",
                "ticket_ids": ["D-1"],
                "ticket_count": 1,
                "recovery_action": "Reconnect the phone",
                "evidence_confidence": "high",
            }
        ],
        {"D-1": {"rca_root_cause": "Bluetooth pairing session timeout"}},
    )
    output_path = tmp_path / "failure-pattern-graph.xlsx"

    write_workbook(graph, output_path)

    workbook = openpyxl.load_workbook(output_path, read_only=True, data_only=True)
    try:
        assert workbook.sheetnames == [
            "00_Overview",
            "01_Failure_Pattern_Graph",
            "02_Failure_Mode_Candidates",
            "03_Action_Catalog",
            "04_Graph_Nodes",
            "05_Graph_Edges",
            "06_Data_Dictionary",
            "07_Failure_Pattern_Tree_Catalog",
            "08_Failure_Pattern_Tree_Nodes",
        ]
        headers = [cell.value for cell in workbook["01_Failure_Pattern_Graph"][1]]
        assert "Cause Probability Status" in headers
        assert "Recorded Failure Mode Candidate" in headers
        first_record = next(workbook["01_Failure_Pattern_Graph"].iter_rows(min_row=2, values_only=True))
        assert "not_estimated" in first_record
        assert "Bluetooth pairing session timeout" in first_record
        tree_headers = [
            cell.value
            for cell in workbook["07_Failure_Pattern_Tree_Catalog"][1]
        ]
        node_headers = [
            cell.value
            for cell in workbook["08_Failure_Pattern_Tree_Nodes"][1]
        ]
        assert {
            "Service Function",
            "Issue Group Count",
            "Trigger Pattern Count",
            "Failure Pattern Count",
            "Diagnostic Layers",
        }.issubset(tree_headers)
        assert {
            "Node ID",
            "Parent Node ID",
            "Node Type",
            "Question / Solution",
            "Solution Status",
        }.issubset(node_headers)
    finally:
        workbook.close()


def test_failure_pattern_graph_builder_supports_direct_script_invocation() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    script_path = repository_root / "backend" / "analytics" / "phase09_failure_pattern_graph_build.py"

    result = subprocess.run(
        [sys.executable, str(script_path), "--help"],
        cwd=repository_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Build a V13 Failure Pattern Graph" in result.stdout


def test_failure_pattern_catalog_allows_unbreakable_evidence_to_wrap_on_mobile() -> None:
    builder = _load_builder_module()

    assert builder is not None
    html = builder.render_failure_pattern_catalog_html({"failure_patterns": []})

    assert "min-width:0" in html
    assert "overflow-wrap:anywhere" in html


def test_failure_pattern_graph_writer_leaves_large_data_cells_unstyled(tmp_path) -> None:
    builder = _load_builder_module()

    assert builder is not None
    graph = builder.build_failure_pattern_graph(
        [
            {
                "service_function_id": "CARPLAY",
                "service_function": "CarPlay",
                "scenario_id": "FS-0001",
                "problem_type": "Connection / Pairing (连接/配对)",
                "customer_symptom": "CarPlay cannot connect to the phone",
                "trigger_condition": "After connecting the phone",
                "ticket_ids": ["D-1"],
                "ticket_count": 1,
            }
        ],
        {"D-1": {"rca_root_cause": "Bluetooth pairing session timeout"}},
    )
    output_path = tmp_path / "failure-pattern-graph.xlsx"

    builder.write_failure_pattern_graph_workbook(graph, output_path)

    workbook = openpyxl.load_workbook(output_path, read_only=False, data_only=True)
    try:
        assert workbook["04_Graph_Nodes"]["A2"].style_id == 0
        assert workbook["05_Graph_Edges"]["A2"].style_id == 0
    finally:
        workbook.close()


def test_v13_renders_a_service_function_decision_tree_for_each_graph_group() -> None:
    builder = _load_builder_module()

    assert builder is not None
    graph = builder.build_failure_pattern_graph(
        [
            {
                "service_function_id": "CARPLAY",
                "service_function": "CarPlay",
                "scenario_id": "FS-0001",
                "problem_type": "Connection / Pairing (连接/配对)",
                "customer_symptom": "CarPlay cannot connect to the phone",
                "trigger_condition": "After connecting the phone",
                "ticket_ids": ["D-1"],
                "ticket_count": 1,
                "recovery_action": "Reconnect the phone",
            },
            {
                "service_function_id": "HICAR",
                "service_function": "HiCar",
                "scenario_id": "FS-0002",
                "problem_type": "Connection / Pairing (连接/配对)",
                "customer_symptom": "HiCar cannot connect to the phone",
                "trigger_condition": "After connecting the phone",
                "ticket_ids": ["D-2"],
                "ticket_count": 1,
            },
        ],
        {
            "D-1": {"rca_root_cause": "Bluetooth pairing session timeout"},
            "D-2": {"rca_root_cause": ""},
        },
    )

    trees = builder.build_service_function_decision_trees(graph)

    tree_by_id = {tree["service_function_id"]: tree for tree in trees}
    assert {"CARPLAY", "HICAR"}.issubset(tree_by_id)
    carplay_tree = tree_by_id["CARPLAY"]
    assert carplay_tree["scenario_count"] == 1
    assert carplay_tree["paths"][0]["failure_mode_candidates"] == [
        "Bluetooth pairing session timeout"
    ]
    assert carplay_tree["paths"][0]["recorded_recovery_action"] == "Reconnect the phone"
    hicar_tree = tree_by_id["HICAR"]
    assert hicar_tree["paths"][0]["causal_evidence_gap"] is True

    html = builder.render_service_function_decision_tree_catalog_html(graph)

    assert 'id="tree-selector"' in html
    assert 'id="scenario-selector"' in html
    assert 'id="decision-tree"' in html
    assert "layoutFailurePatternTree" in html
    assert "Service Function -&gt; Scenario -&gt; Failure Pattern" in html


def test_default_v13_catalog_is_a_safe_failure_pattern_tree_view() -> None:
    builder = _load_builder_module()

    assert builder is not None
    graph = builder.build_failure_pattern_graph(
        [
            {
                "service_function_id": "CARPLAY",
                "service_function": "CarPlay",
                "scenario_id": "FS-0001",
                "problem_type": "Connection / Pairing (连接/配对)",
                "customer_symptom": "</SCRIPT><script>window.injected=1</script>",
                "trigger_condition": "After connecting the phone",
                "ticket_ids": ["D-1"],
                "ticket_count": 1,
            }
        ],
        {"D-1": {"rca_root_cause": ""}},
    )

    html = builder.render_failure_pattern_catalog_html(graph)

    assert 'id="tree-selector"' in html
    assert 'id="issue-selector"' in html
    assert 'id="trigger-selector"' in html
    assert 'id="scenario-selector"' not in html
    assert 'type="application/json"' in html
    assert "Service Function -&gt; Issue Group -&gt; Trigger Pattern -&gt; Failure Pattern -&gt; Ticket Solution" in html
    assert "\\u003c/SCRIPT\\u003e" in html
    assert "</SCRIPT><script>window.injected=1</script>" not in html


def test_failure_pattern_tree_groups_a_trigger_by_customer_visible_failure() -> None:
    builder = _load_builder_module()

    assert builder is not None
    graph = builder.build_failure_pattern_graph(
        [
            {
                "service_function_id": "CARPLAY",
                "service_function": "CarPlay",
                "scenario_id": "FS-0001",
                "problem_type": "Connection / Pairing (连接/配对)",
                "customer_symptom": "CarPlay无法连接手机",
                "trigger_condition": "连接手机时",
                "ticket_ids": ["D-1"],
                "ticket_count": 1,
                "rep_ticket_id": "D-1",
                "recovery_action": "重新连接USB线",
            },
            {
                "service_function_id": "CARPLAY",
                "service_function": "CarPlay",
                "scenario_id": "FS-0002",
                "problem_type": "Connection / Pairing (连接/配对)",
                "customer_symptom": "CarPlay功能不可用",
                "trigger_condition": "连接手机时",
                "ticket_ids": ["D-2"],
                "ticket_count": 1,
                "rep_ticket_id": "D-2",
                "recovery_action": "重新启动车机",
            },
        ],
        {
            "D-1": {"rca_root_cause": ""},
            "D-2": {"rca_root_cause": ""},
        },
    )

    trees, nodes = builder.build_failure_pattern_trees(graph)

    carplay_tree = next(
        tree for tree in trees if tree["service_function_id"] == "CARPLAY"
    )
    carplay_nodes = [node for node in nodes if node["tree_id"] == carplay_tree["tree_id"]]
    issue = next(node for node in carplay_nodes if node["node_type"] == "Issue Group")
    trigger = next(node for node in carplay_nodes if node["node_type"] == "Trigger Pattern")
    failure_patterns = [
        node for node in carplay_nodes if node["node_type"] == "Failure Pattern"
    ]
    ticket_solutions = [
        node for node in carplay_nodes if node["node_type"] == "Ticket Solution"
    ]

    assert carplay_tree["diagnostic_layers"] == [
        "Issue Group",
        "Trigger Pattern",
        "Failure Pattern",
        "Ticket Solution",
    ]
    assert issue["question_or_action"] == "Connection / Pairing (连接/配对)"
    assert trigger["parent_node_id"] == issue["node_id"]
    assert trigger["question_or_action"] == "During connection"
    assert {
        "Connection cannot be established (无法连接)",
        "Feature unavailable (功能不可用)",
    } == {node["question_or_action"] for node in failure_patterns}
    assert {node["parent_node_id"] for node in failure_patterns} == {trigger["node_id"]}
    assert {node["parent_node_id"] for node in ticket_solutions}.issubset(
        {node["node_id"] for node in failure_patterns}
    )
    assert "Scenario" not in {node["node_type"] for node in carplay_nodes}


def test_failure_pattern_tree_uses_v12_observed_behaviour_for_classification() -> None:
    builder = _load_builder_module()

    assert builder is not None
    graph = builder.build_failure_pattern_graph(
        [
            {
                "service_function_id": "CARPLAY",
                "service_function": "CarPlay",
                "scenario_id": "FS-0001",
                "problem_type": "Audio / Media (声音/媒体)",
                "customer_symptom": "CarPlay experience is abnormal",
                "observed_behaviour": "No audio output",
                "trigger_condition": "连接手机后",
                "ticket_ids": ["D-1"],
                "ticket_count": 1,
            }
        ],
        {"D-1": {"rca_root_cause": ""}},
    )

    _, nodes = builder.build_failure_pattern_trees(graph)

    assert "No audio / media output (无声音)" in {
        node["question_or_action"]
        for node in nodes
        if node["node_type"] == "Failure Pattern"
    }


def test_failure_pattern_tree_renderer_anchors_parent_path_at_first_failure() -> None:
    builder = _load_builder_module()

    assert builder is not None
    html = builder.render_failure_pattern_tree_catalog_html({"failure_patterns": []})

    assert "const anchorY = 120" in html
    assert "const y = anchorY + index * 145" in html
    assert "const centerY = height / 2" not in html


def test_failure_pattern_tree_renderer_contains_tree_width_inside_its_scroller() -> None:
    builder = _load_builder_module()

    assert builder is not None
    html = builder.render_failure_pattern_tree_catalog_html({"failure_patterns": []})

    assert "html,body { overflow-x:hidden; }" in html
    assert ".diagram-shell { min-width:0; max-width:100%;" in html


def test_v13_service_function_tree_catalog_keeps_every_configured_v12_entry() -> None:
    builder = _load_builder_module()

    assert builder is not None
    graph = builder.build_failure_pattern_graph(
        [
            {
                "service_function_id": "CARPLAY",
                "service_function": "CarPlay",
                "scenario_id": "FS-0001",
                "problem_type": "Connection / Pairing (连接/配对)",
                "customer_symptom": "CarPlay cannot connect",
                "trigger_condition": "After connecting the phone",
                "ticket_ids": ["D-1"],
                "ticket_count": 1,
            }
        ],
        {"D-1": {"rca_root_cause": ""}},
    )

    trees = builder.build_service_function_decision_trees(graph)

    assert len(trees) == len(builder.SERVICE_FUNCTION_CATALOG)
    assert [tree["service_function_id"] for tree in trees] == [
        service_function["service_function_id"]
        for service_function in builder.SERVICE_FUNCTION_CATALOG
    ]
    tree_by_id = {tree["service_function_id"]: tree for tree in trees}
    assert tree_by_id["CARPLAY"]["scenario_count"] == 1
    assert tree_by_id["HICAR"]["scenario_count"] == 0
    assert tree_by_id["TTS"]["paths"] == []


def test_graph_keeps_parallel_evidence_branches_and_ticket_provenance() -> None:
    builder = _load_builder_module()

    assert builder is not None
    graph = builder.build_failure_pattern_graph(
        [
            {
                "service_function_id": "CARPLAY",
                "service_function": "CarPlay",
                "scenario_id": "FS-0001",
                "problem_type": "Connection / Pairing (连接/配对)",
                "customer_symptom": "CarPlay cannot connect",
                "trigger_condition": "After connecting the phone",
                "ticket_ids": ["D-1", "D-2"],
                "ticket_count": 2,
                "rep_ticket_id": "D-1",
                "recovery_action": "Reconnect the phone",
            }
        ],
        {
            "D-1": {"rca_root_cause": "Bluetooth pairing session timeout"},
            "D-2": {"rca_root_cause": ""},
        },
    )

    assert graph["model_path"] == (
        "Service Function -> Issue Group -> Trigger Pattern -> Failure Pattern -> "
        "Ticket Solution"
    )
    assert graph["audit_graph_path"] == (
        "Service Function -> Scenario -> Failure Pattern -> "
        "{Recorded Failure Mode Candidate / Causal Evidence Gap, "
        "Recorded Recovery Action}"
    )
    candidate = graph["failure_mode_candidates"][0]
    action = graph["action_records"][0]
    assert candidate["source_ticket_ids"] == ["D-1"]
    assert action["source_ticket_ids"] == ["D-1"]
    assert action["evidence_source"] == "V12 01_Scenario_Library / Recorded Recovery Action"

    node_types = {node["node_id"]: node["node_type"] for node in graph["nodes"]}
    action_edge = next(
        edge
        for edge in graph["edges"]
        if edge["relation_type"] == "has_recorded_recovery_action"
    )
    assert node_types[action_edge["source_node_id"]] == "Failure Pattern"
    assert node_types[action_edge["target_node_id"]] == "Recorded Recovery Action"
    assert action_edge["source_ticket_ids"] == ["D-1"]
    carplay_tree = next(
        tree
        for tree in builder.build_service_function_decision_trees(graph)
        if tree["service_function_id"] == "CARPLAY"
    )
    tree_path = carplay_tree["paths"][0]
    assert tree_path["recorded_recovery_source_ticket_ids"] == ["D-1"]
    assert tree_path["failure_mode_candidate_details"][0]["source_ticket_ids"] == [
        "D-1"
    ]


def test_graph_rejects_missing_source_ticket_evidence() -> None:
    builder = _load_builder_module()

    assert builder is not None
    with pytest.raises(ValueError, match="Missing source ticket evidence"):
        builder.build_failure_pattern_graph(
            [
                {
                    "service_function_id": "CARPLAY",
                    "service_function": "CarPlay",
                    "scenario_id": "FS-0001",
                    "customer_symptom": "CarPlay cannot connect",
                    "trigger_condition": "After connecting the phone",
                    "ticket_ids": ["D-1"],
                    "ticket_count": 1,
                }
            ],
            {},
        )


def test_recorded_recovery_text_preserves_a_recorded_unresolved_outcome() -> None:
    builder = _load_builder_module()

    assert builder is not None
    recovery = "Reconnect performed, but the issue remained unresolved"

    assert builder._recorded_recovery_text(recovery) == recovery


def test_load_v12_scenarios_rejects_incomplete_ticket_mapping(tmp_path) -> None:
    builder = _load_builder_module()

    assert builder is not None
    scenario_workbook = tmp_path / "v12.xlsx"
    knowledge_base = tmp_path / "v12.json"
    workbook = openpyxl.Workbook()
    scenario_sheet = workbook.active
    scenario_sheet.title = "01_Scenario_Library"
    scenario_sheet.append([
        "Service Function",
        "Scenario ID",
        "Problem Type",
        "Customer Symptom",
        "Trigger Condition",
        "Ticket Count",
        "Recorded Recovery Action",
        "Evidence Confidence",
        "Rep Ticket ID",
    ])
    scenario_sheet.append([
        "CarPlay",
        "FS-0001",
        "Connection / Pairing (连接/配对)",
        "CarPlay cannot connect",
        "After connecting the phone",
        2,
        "Reconnect the phone",
        "high",
        "D-1",
    ])
    mapping_sheet = workbook.create_sheet("07_Ticket_Mapping")
    mapping_sheet.append(["Ticket ID", "Scenario ID"])
    mapping_sheet.append(["D-1", "FS-0001"])
    workbook.save(scenario_workbook)
    knowledge_base.write_text(
        json.dumps({
            "decision_trees": [
                {"service_function": "CarPlay", "service_function_id": "CARPLAY"}
            ]
        }),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Ticket mapping count mismatch"):
        builder.load_v12_scenarios(scenario_workbook, knowledge_base)


def test_graph_workbook_exports_candidate_and_recovery_ticket_provenance(tmp_path) -> None:
    builder = _load_builder_module()

    assert builder is not None
    graph = builder.build_failure_pattern_graph(
        [
            {
                "service_function_id": "CARPLAY",
                "service_function": "CarPlay",
                "scenario_id": "FS-0001",
                "problem_type": "Connection / Pairing (连接/配对)",
                "customer_symptom": "CarPlay cannot connect",
                "trigger_condition": "After connecting the phone",
                "ticket_ids": ["D-1"],
                "ticket_count": 1,
                "rep_ticket_id": "D-1",
                "recovery_action": "Reconnect the phone",
            }
        ],
        {"D-1": {"rca_root_cause": "Bluetooth pairing session timeout"}},
    )
    output_path = tmp_path / "failure-pattern-graph.xlsx"

    builder.write_failure_pattern_graph_workbook(graph, output_path)

    workbook = openpyxl.load_workbook(output_path, read_only=True, data_only=True)
    try:
        candidate_rows = workbook["02_Failure_Mode_Candidates"].iter_rows(
            values_only=True
        )
        candidate_headers = next(candidate_rows)
        candidate_values = next(candidate_rows)
        action_rows = workbook["03_Action_Catalog"].iter_rows(values_only=True)
        action_headers = next(action_rows)
        action_values = next(action_rows)
        assert "Source Ticket IDs" in candidate_headers
        assert candidate_values[candidate_headers.index("Source Ticket IDs")] == "D-1"
        assert "Source Ticket IDs" in action_headers
        assert action_values[action_headers.index("Source Ticket IDs")] == "D-1"
    finally:
        workbook.close()