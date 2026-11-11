import json

from backend.analytics.phase09_service_function_diagnosis_tree_catalog import (
    build_service_function_tree,
    build_service_function_tree_catalog_from_scenario_catalog,
    render_service_function_tree_catalog_html,
    write_service_function_tree_catalog_artifacts,
)


def _scenario_path(
    scenario_id: str,
    symptom: str,
    trigger: str,
    ticket_count: int,
    branch: dict,
    problem_type: str = "Connection / Pairing",
) -> dict:
    return {
        "scenario_id": scenario_id,
        "customer_symptom": symptom,
        "trigger_condition": trigger,
        "observed_behaviour": f"Observed {symptom}",
        "expected_behaviour": f"Expected {symptom}",
        "representative_ticket_id": f"D-{scenario_id}",
        "source_ticket_ids": [f"D-{scenario_id}"],
        "source_ticket_count": ticket_count,
        "problem_type": problem_type,
        "risk_band": "Medium",
        "risk_index": 2.5,
        "evidence_confidence": "high",
        "diagnostic_branches": [branch],
    }


def _source_branch(source_path: str, cause: str = "USB cable fault") -> dict:
    return {
        "branch_id": f"EXP-{source_path}",
        "basis": "after_sales_experience_source_path",
        "status": "source_experience_reference_candidate",
        "match_score": 0.5,
        "possible_cause": cause,
        "check_steps": "Check the source route.",
        "resolution": "Repair the source route.",
        "resolution_status": "after_sales_source_experience_reference",
        "escalation": "L1 to L3",
        "source_reference": {
            "reference_tree_id": "DTREF-CARPLAY",
            "source_tree_id": "CARPLAY",
            "source_node_id": "SRC-USB",
            "source_path": source_path,
            "source_asset": "decision_tree_sources/lossless_webp/carplay.webp",
        },
        "ticket_evidence": {},
    }


def _evidence_branch() -> dict:
    return {
        "branch_id": "EVIDENCE",
        "basis": "evidence_collection_template",
        "status": "no_verified_causal_evidence",
        "match_score": 0.0,
        "possible_cause": "No verified technical cause recorded",
        "check_steps": "Collect logs and reproduce the exact trigger.",
        "resolution": "",
        "resolution_status": "no_recorded_recovery",
        "escalation": "Prepare an engineering evidence package",
        "source_reference": {},
        "ticket_evidence": {},
    }


def test_service_function_tree_contains_all_scenarios_once_under_shared_routes() -> None:
    function = {
        "tree_id": "DT-CARPLAY",
        "service_function_id": "CARPLAY",
        "service_function": "CarPlay",
        "service_function_label": "CarPlay",
        "service_domain": "Phone Projection",
        "catalog_status": "after_sales_reference",
        "source_graphs": [],
        "scenario_paths": [
            _scenario_path(
                "FS-0001",
                "CarPlay cannot connect through USB",
                "After cable connection",
                3,
                _source_branch(
                    "CARPLAY > CarPlay connection issue > USB wired connection failure"
                ),
            ),
            _scenario_path(
                "FS-0002",
                "CarPlay cannot connect wirelessly",
                "After wireless pairing",
                2,
                _source_branch(
                    "CARPLAY > CarPlay connection issue > Wireless connection failure",
                    "Bluetooth or WiFi pairing fault",
                ),
            ),
            _scenario_path(
                "FS-0003",
                "CarPlay audio is silent",
                "After connection",
                1,
                _evidence_branch(),
                problem_type="Audio / Media",
            ),
        ],
    }

    tree = build_service_function_tree(function)

    scenario_nodes = [
        node for node in tree["nodes"] if node["node_type"] == "Failure Scenario"
    ]
    assert {node["scenario_id"] for node in scenario_nodes} == {
        "FS-0001",
        "FS-0002",
        "FS-0003",
    }
    assert len(scenario_nodes) == 3
    assert len(tree["routes"]) == 2
    source_route = next(
        route for route in tree["routes"]
        if route["route_type"] == "after_sales_experience_route"
    )
    field_route = next(
        route for route in tree["routes"]
        if route["route_type"] == "field_failure_route"
    )
    assert source_route["scenario_ids"] == ["FS-0001", "FS-0002"]
    assert "CarPlay connection issue" in source_route["label"]
    assert field_route["scenario_ids"] == ["FS-0003"]
    assert "Audio / Media" in field_route["label"]

    node_by_id = {node["node_id"]: node for node in tree["nodes"]}
    fs_0001 = next(node for node in scenario_nodes if node["scenario_id"] == "FS-0001")
    trigger = node_by_id[fs_0001["next_node_id"]]
    cause = node_by_id[trigger["next_node_id"]]
    check = node_by_id[cause["next_node_id"]]
    outcome = node_by_id[check["next_node_id"]]
    assert trigger["node_type"] == "Trigger Condition"
    assert cause["node_type"] == "Possible Cause"
    assert cause["label"] == "USB cable fault"
    assert check["node_type"] == "Check Steps"
    assert outcome["node_type"] == "Solution / Escalation"
    assert len(tree["edges"]) == len(tree["nodes"]) - 1


def test_primary_route_prefers_after_sales_experience_over_ticket_candidate() -> None:
    ticket_branch = {
        **_evidence_branch(),
        "branch_id": "RCA",
        "basis": "ticket_recorded_rca_candidate",
        "status": "ticket_recorded_candidate_review_required",
        "possible_cause": "Recorded ticket root cause candidate",
        "resolution": "Recorded workaround",
        "resolution_status": "ticket_recorded_workaround_not_approved",
    }
    function = {
        "tree_id": "DT-CARPLAY",
        "service_function_id": "CARPLAY",
        "service_function": "CarPlay",
        "service_function_label": "CarPlay",
        "service_domain": "Phone Projection",
        "catalog_status": "after_sales_reference",
        "source_graphs": [],
        "scenario_paths": [
            {
                **_scenario_path(
                    "FS-0001",
                    "CarPlay cannot connect through USB",
                    "After cable connection",
                    1,
                    _source_branch(
                        "CARPLAY > CarPlay connection issue > USB wired connection failure"
                    ),
                ),
                "diagnostic_branches": [
                    ticket_branch,
                    _source_branch(
                        "CARPLAY > CarPlay connection issue > USB wired connection failure"
                    ),
                ],
            }
        ],
    }

    tree = build_service_function_tree(function)

    cause = next(
        node for node in tree["nodes"] if node["node_type"] == "Possible Cause"
    )
    assert cause["basis"] == "after_sales_experience_source_path"
    assert cause["label"] == "USB cable fault"
    scenario = next(
        node for node in tree["nodes"] if node["node_type"] == "Failure Scenario"
    )
    assert scenario["alternate_branch_count"] == 1


def test_unified_tree_does_not_duplicate_v2_branch_nodes_per_case() -> None:
    source_branch = {
        **_source_branch(
            "CARPLAY > CarPlay connection issue > USB wired connection failure"
        ),
        "nodes": [
            {"node_type": "Possible Cause"},
            {"node_type": "Check Steps"},
            {"node_type": "Solution / Escalation"},
        ],
    }
    function = {
        "tree_id": "DT-CARPLAY",
        "service_function_id": "CARPLAY",
        "service_function": "CarPlay",
        "service_function_label": "CarPlay",
        "service_domain": "Phone Projection",
        "catalog_status": "after_sales_reference",
        "source_graphs": [],
        "scenario_paths": [
            _scenario_path(
                "FS-0001", "Cannot connect", "During connection", 1, source_branch
            )
        ],
    }

    tree = build_service_function_tree(function)

    scenario_case = tree["scenario_cases"][0]
    assert "nodes" not in scenario_case["primary_branch"]
    assert all("nodes" not in branch for branch in scenario_case["alternate_branches"])
    assert next(
        node for node in tree["nodes"] if node["node_type"] == "Possible Cause"
    )["label"] == "USB cable fault"


def test_catalog_keeps_all_function_scenarios_in_one_tree_per_function() -> None:
    catalog = {
        "summary": {"field_ticket_count": 3},
        "functions": [
            {
                "tree_id": "DT-CARPLAY",
                "service_function_id": "CARPLAY",
                "service_function": "CarPlay",
                "service_function_label": "CarPlay",
                "service_domain": "Phone Projection",
                "catalog_status": "after_sales_reference",
                "source_graphs": [],
                "scenario_paths": [
                    _scenario_path(
                        "FS-0001", "Cannot connect", "During connection", 2,
                        _source_branch("CARPLAY > Connection issue > USB failure"),
                    ),
                    _scenario_path(
                        "FS-0002", "No audio", "After connection", 1,
                        _evidence_branch(), "Audio / Media",
                    ),
                ],
            },
            {
                "tree_id": "DT-NEW",
                "service_function_id": "NEW",
                "service_function": "New Feature",
                "service_function_label": "New Feature",
                "service_domain": "New Domain",
                "catalog_status": "feature",
                "source_graphs": [],
                "scenario_paths": [],
            },
        ],
    }

    result = build_service_function_tree_catalog_from_scenario_catalog(catalog)

    assert result["summary"]["service_function_count"] == 2
    assert result["summary"]["scenario_count"] == 2
    carplay = next(
        item for item in result["functions"]
        if item["service_function_id"] == "CARPLAY"
    )
    assert carplay["unified_tree"]["scenario_count"] == 2
    assert carplay["unified_tree"]["ticket_count"] == 3
    assert len(carplay["unified_tree"]["routes"]) == 2
    html = render_service_function_tree_catalog_html(result)
    assert "Unified Service Function Tree" in html
    assert "All scenarios in the selected Service Function" in html
    assert "Cannot connect" in html


def test_artifact_writer_emits_html_and_json(tmp_path) -> None:
    catalog = {
        "schema_version": "phase09-service-function-diagnosis-tree-catalog-v3",
        "summary": {"scenario_count": 0},
        "functions": [],
    }
    output_html = tmp_path / "catalog.html"
    output_json = tmp_path / "catalog.json"

    write_service_function_tree_catalog_artifacts(
        catalog=catalog,
        output_html=output_html,
        output_json=output_json,
    )

    assert output_html.is_file()
    assert output_json.is_file()
    assert json.loads(output_json.read_text(encoding="utf-8")) == catalog
    assert "Unified Service Function Tree" in output_html.read_text(encoding="utf-8")


def test_rendered_html_defaults_failure_scenario_selector_to_all_cases() -> None:
    html = render_service_function_tree_catalog_html({
        "schema_version": "phase09-service-function-diagnosis-tree-catalog-v3",
        "summary": {},
        "functions": [],
    })

    assert "All failure scenarios (${cases.length} scenarios)" in html
    assert 'allOption.value=""' in html
    assert 'activeScenarioId="";selector.value=activeScenarioId;' in html