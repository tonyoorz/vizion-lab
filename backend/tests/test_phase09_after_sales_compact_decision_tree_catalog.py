import json
from pathlib import Path
import subprocess
import sys

from backend.analytics.phase09_after_sales_compact_decision_tree_catalog import (
    build_compact_after_sales_tree,
    build_compact_decision_tree_catalog_from_scenario_catalog,
    render_compact_after_sales_decision_tree_catalog_html,
    write_compact_decision_tree_catalog_artifacts,
)


def _source_branch(source_node_id: str, source_path: str) -> dict:
    return {
        "branch_id": f"EXP-{source_node_id}",
        "basis": "after_sales_experience_source_path",
        "status": "source_experience_reference_candidate",
        "match_score": 0.5,
        "possible_cause": "USB cable or vehicle USB port fault",
        "check_steps": "Check the USB cable and test another vehicle USB port.",
        "resolution": "Replace the data-capable cable or repair the USB port.",
        "resolution_status": "after_sales_source_experience_reference",
        "escalation": "L1 to L3 dealer diagnostics",
        "source_reference": {
            "reference_tree_id": "DTREF-CARPLAY",
            "source_tree_id": "CARPLAY",
            "source_node_id": source_node_id,
            "source_path": source_path,
            "source_asset": "decision_tree_sources/lossless_webp/carplay.webp",
        },
        "ticket_evidence": {},
    }


def _evidence_branch(recovery: str = "") -> dict:
    return {
        "branch_id": "EVIDENCE",
        "basis": "evidence_collection_template",
        "status": "no_verified_causal_evidence",
        "possible_cause": "No verified technical cause recorded",
        "check_steps": "Collect logs and reproduce the exact trigger.",
        "resolution": recovery,
        "resolution_status": (
            "ticket_recorded_workaround_not_approved"
            if recovery
            else "no_recorded_recovery"
        ),
        "escalation": "Prepare an engineering evidence package",
        "source_reference": {},
        "ticket_evidence": {},
    }


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


def _source_graph() -> dict:
    return {
        "tree_id": "CARPLAY",
        "reference_tree_id": "DTREF-CARPLAY",
        "tree_name_en": "CarPlay",
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
                "node_id": "SRC-CONNECT",
                "parent_node_id": "ROOT",
                "node_type": "decision",
                "source_text": "CarPlay connection issue",
                "source_path": "CARPLAY > CarPlay connection issue",
                "is_terminal": False,
            },
            {
                "node_id": "SRC-USB",
                "parent_node_id": "SRC-CONNECT",
                "node_type": "resolution",
                "source_text": "USB wired connection failure",
                "source_path": "CARPLAY > CarPlay connection issue > USB wired connection failure",
                "symptom": "CarPlay cannot connect through USB",
                "possible_cause": "USB cable or vehicle USB port fault",
                "check_step": "Check the USB cable and test another vehicle USB port.",
                "solution": "Replace the data-capable cable or repair the USB port.",
                "escalation_path": "L1 to L3 dealer diagnostics",
                "is_terminal": True,
            },
            {
                "node_id": "SRC-WIFI",
                "parent_node_id": "SRC-CONNECT",
                "node_type": "resolution",
                "source_text": "Wireless connection failure",
                "source_path": "CARPLAY > CarPlay connection issue > Wireless connection failure",
                "symptom": "CarPlay cannot connect wirelessly",
                "possible_cause": "Bluetooth or WiFi pairing fault",
                "check_step": "Check pairing and wireless transport state.",
                "solution": "Repair pairing or wireless transport configuration.",
                "escalation_path": "L1 to L3 dealer diagnostics",
                "is_terminal": True,
            },
        ],
        "edges": [
            {"from_node_id": "ROOT", "to_node_id": "SRC-CONNECT"},
            {"from_node_id": "SRC-CONNECT", "to_node_id": "SRC-USB"},
            {"from_node_id": "SRC-CONNECT", "to_node_id": "SRC-WIFI"},
        ],
    }


def test_source_tree_compacts_to_route_and_terminal_diagnoses() -> None:
    function = {
        "service_function_id": "CARPLAY",
        "service_function": "CarPlay",
        "service_function_label": "CarPlay",
        "service_domain": "Phone Projection",
        "source_graphs": [_source_graph()],
        "scenario_paths": [
            _scenario_path(
                "FS-0001",
                "CarPlay cannot connect through USB",
                "After cable connection",
                3,
                _source_branch(
                    "SRC-USB",
                    "CARPLAY > CarPlay connection issue > USB wired connection failure",
                ),
            ),
            _scenario_path(
                "FS-0002",
                "CarPlay cannot connect wirelessly",
                "After wireless pairing",
                2,
                _source_branch(
                    "SRC-WIFI",
                    "CARPLAY > CarPlay connection issue > Wireless connection failure",
                ),
            ),
        ],
    }

    tree = build_compact_after_sales_tree(function)

    assert not any(node["node_type"] == "Failure Scenario" for node in tree["nodes"])
    route_nodes = [
        node for node in tree["nodes"] if node["node_type"] == "Source Complaint Route"
    ]
    diagnosis_nodes = [
        node for node in tree["nodes"] if node["node_type"] == "Source Diagnosis"
    ]
    assert len(route_nodes) == 1
    assert route_nodes[0]["label"] == "CarPlay connection issue"
    assert len(diagnosis_nodes) == 2
    usb = next(node for node in diagnosis_nodes if node["source_node_id"] == "SRC-USB")
    assert usb["possible_cause"] == "USB cable or vehicle USB port fault"
    assert usb["check_steps"] == "Check the USB cable and test another vehicle USB port."
    assert usb["resolution"] == "Replace the data-capable cable or repair the USB port."
    assert usb["source_scenario_count"] == 1
    assert len(tree["scenario_evidence"]) == 2
    assert {
        item["assigned_node_id"] for item in tree["scenario_evidence"]
    } == {"DIAG-CARPLAY-SRC-USB", "DIAG-CARPLAY-SRC-WIFI"}
    assert len(tree["nodes"]) == 4


def test_field_only_function_uses_compact_question_to_outcome_routes() -> None:
    function = {
        "service_function_id": "NEW_FEATURE",
        "service_function": "New Feature",
        "service_function_label": "New Feature",
        "service_domain": "New Domain",
        "source_graphs": [],
        "scenario_paths": [
            _scenario_path(
                "FS-0001", "Feature cannot connect", "During pairing", 2,
                _evidence_branch(), "Connection / Pairing",
            ),
            _scenario_path(
                "FS-0002", "Feature cannot connect after wake-up", "After wake-up", 1,
                _evidence_branch(), "Connection / Pairing",
            ),
            _scenario_path(
                "FS-0003", "Feature has no audio", "During playback", 1,
                _evidence_branch("Restart the feature"), "Audio / Media",
            ),
        ],
    }

    tree = build_compact_after_sales_tree(function)

    routes = [
        node for node in tree["nodes"] if node["node_type"] == "Field Complaint Route"
    ]
    checks = [
        node for node in tree["nodes"] if node["node_type"] == "Compact Check"
    ]
    outcomes = [
        node for node in tree["nodes"] if node["node_type"] == "Field Outcome"
    ]
    assert len(routes) == 2
    assert len(checks) == 2
    assert len(outcomes) == 4
    connection_check = next(
        node for node in checks if "Connection / Pairing" in node["question"]
    )
    assert connection_check["yes_next_node"]
    assert connection_check["no_next_node"]
    assert {edge["outcome"] for edge in tree["edges"]} >= {"YES", "NO"}
    assert len(tree["scenario_evidence"]) == 3
    assert all(
        item["assigned_node_id"] in {node["node_id"] for node in routes}
        for item in tree["scenario_evidence"]
    )


def test_catalog_keeps_all_scenarios_as_evidence_under_one_compact_tree() -> None:
    scenario_catalog = {
        "summary": {"field_ticket_count": 4},
        "functions": [
            {
                "service_function_id": "CARPLAY",
                "service_function": "CarPlay",
                "service_function_label": "CarPlay",
                "service_domain": "Phone Projection",
                "source_graphs": [_source_graph()],
                "scenario_paths": [
                    _scenario_path(
                        "FS-0001", "CarPlay cannot connect through USB", "After cable connection", 3,
                        _source_branch(
                            "SRC-USB",
                            "CARPLAY > CarPlay connection issue > USB wired connection failure",
                        ),
                    )
                ],
            },
            {
                "service_function_id": "NEW_FEATURE",
                "service_function": "New Feature",
                "service_function_label": "New Feature",
                "service_domain": "New Domain",
                "source_graphs": [],
                "scenario_paths": [
                    _scenario_path(
                        "FS-0002", "Feature unavailable", "After activation", 1,
                        _evidence_branch(), "Authentication / Account",
                    )
                ],
            },
        ],
    }

    catalog = build_compact_decision_tree_catalog_from_scenario_catalog(scenario_catalog)

    assert catalog["summary"]["service_function_count"] == 2
    assert catalog["summary"]["compact_tree_count"] == 2
    assert catalog["summary"]["scenario_evidence_count"] == 2
    assert catalog["summary"]["field_ticket_count"] == 4
    assert all(
        len(function["compact_tree"]["scenario_evidence"])
        == function["compact_tree"]["scenario_count"]
        for function in catalog["functions"]
    )
    html = render_compact_after_sales_decision_tree_catalog_html(catalog)
    assert "Compact After-Sales Decision Tree" in html
    assert "Historical Scenario Evidence" in html
    assert "Source Complaint Route" in html


def test_artifact_writer_and_direct_script_entry(tmp_path) -> None:
    catalog = {
        "schema_version": "phase09-after-sales-compact-decision-tree-v5",
        "summary": {"scenario_evidence_count": 0},
        "functions": [],
    }
    output_html = tmp_path / "catalog.html"
    output_json = tmp_path / "catalog.json"

    write_compact_decision_tree_catalog_artifacts(
        catalog=catalog,
        output_html=output_html,
        output_json=output_json,
    )

    assert output_html.is_file()
    assert output_json.is_file()
    assert json.loads(output_json.read_text(encoding="utf-8")) == catalog
    assert "Compact After-Sales Decision Tree" in output_html.read_text(encoding="utf-8")

    repository_root = Path(__file__).resolve().parents[2]
    script_path = (
        repository_root
        / "backend"
        / "analytics"
        / "phase09_after_sales_compact_decision_tree_catalog.py"
    )
    result = subprocess.run(
        [sys.executable, str(script_path), "--help"],
        cwd=repository_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "compact after-sales decision tree catalog" in result.stdout