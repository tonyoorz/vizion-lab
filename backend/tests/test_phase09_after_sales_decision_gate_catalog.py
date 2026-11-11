import json
from pathlib import Path
import subprocess
import sys

from backend.analytics.phase09_after_sales_decision_gate_catalog import (
    build_after_sales_decision_gate_tree,
    build_decision_gate_catalog_from_scenario_catalog,
    render_after_sales_decision_gate_catalog_html,
    write_after_sales_decision_gate_catalog_artifacts,
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


def _evidence_branch() -> dict:
    return {
        "branch_id": "EVIDENCE",
        "basis": "evidence_collection_template",
        "status": "no_verified_causal_evidence",
        "possible_cause": "No verified technical cause recorded",
        "check_steps": "Collect logs and reproduce the exact trigger.",
        "resolution": "",
        "resolution_status": "no_recorded_recovery",
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


def _carplay_source_graph() -> dict:
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


def test_source_tree_becomes_yes_no_checks_with_historical_evidence() -> None:
    function = {
        "service_function_id": "CARPLAY",
        "service_function": "CarPlay",
        "service_function_label": "CarPlay",
        "service_domain": "Phone Projection",
        "source_graphs": [_carplay_source_graph()],
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

    tree = build_after_sales_decision_gate_tree(function)

    assert not any(node["node_type"] == "Failure Scenario" for node in tree["nodes"])
    assert len(tree["scenario_evidence"]) == 2
    assert {
        evidence["assigned_gate_id"] for evidence in tree["scenario_evidence"]
    } == {"GATE-CARPLAY-SRC-USB", "GATE-CARPLAY-SRC-WIFI"}
    usb_gate = next(
        node for node in tree["nodes"] if node["node_id"] == "GATE-CARPLAY-SRC-USB"
    )
    wifi_gate = next(
        node for node in tree["nodes"] if node["node_id"] == "GATE-CARPLAY-SRC-WIFI"
    )
    assert usb_gate["node_type"] == "Question Gate"
    assert "Does evidence confirm" in usb_gate["question"]
    assert usb_gate["check_steps"] == (
        "Check the USB cable and test another vehicle USB port."
    )
    assert usb_gate["yes_next_node"].startswith("RES-CARPLAY-SRC-USB")
    assert usb_gate["no_next_node"] == wifi_gate["node_id"]
    resolution = next(
        node for node in tree["nodes"] if node["node_id"] == usb_gate["yes_next_node"]
    )
    assert resolution["node_type"] == "Resolution"
    assert resolution["label"] == (
        "Replace the data-capable cable or repair the USB port."
    )
    assert usb_gate["source_ticket_count"] == 3
    assert len(tree["edges"]) >= 5
    assert {edge["outcome"] for edge in tree["edges"]} >= {"YES", "NO"}


def test_unmatched_scenarios_use_one_field_gate_chain_not_scenario_nodes() -> None:
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
                _evidence_branch(), "Audio / Media",
            ),
        ],
    }

    tree = build_after_sales_decision_gate_tree(function)

    issue_gates = [
        node for node in tree["nodes"] if node["node_type"] == "Issue Gate"
    ]
    assert len(issue_gates) == 2
    connection_gate = next(
        node for node in issue_gates if "Connection / Pairing" in node["question"]
    )
    audio_gate = next(
        node for node in issue_gates if "Audio / Media" in node["question"]
    )
    assert connection_gate["yes_next_node"].startswith("PRE-NEW_FEATURE-")
    assert connection_gate["no_next_node"] == audio_gate["node_id"]
    assert all(
        evidence["assigned_gate_id"] in {
            connection_gate["node_id"],
            audio_gate["node_id"],
        }
        for evidence in tree["scenario_evidence"]
    )
    assert any(node["node_type"] == "Precondition Gate" for node in tree["nodes"])
    assert any(node["node_type"] == "Reproduction Gate" for node in tree["nodes"])
    assert any(node["node_type"] == "Technical Evidence Gate" for node in tree["nodes"])
    assert any(node["node_type"] == "Recovery Candidate Gate" for node in tree["nodes"])
    assert any(node["node_type"] == "Escalation" for node in tree["nodes"])


def test_catalog_contains_one_gate_tree_per_function_and_all_scenarios_as_evidence() -> None:
    scenario_catalog = {
        "summary": {"field_ticket_count": 4},
        "functions": [
            {
                "service_function_id": "CARPLAY",
                "service_function": "CarPlay",
                "service_function_label": "CarPlay",
                "service_domain": "Phone Projection",
                "source_graphs": [_carplay_source_graph()],
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

    catalog = build_decision_gate_catalog_from_scenario_catalog(scenario_catalog)

    assert catalog["summary"]["service_function_count"] == 2
    assert catalog["summary"]["decision_gate_tree_count"] == 2
    assert catalog["summary"]["scenario_evidence_count"] == 2
    assert catalog["summary"]["field_ticket_count"] == 4
    assert all(
        len(function["decision_gate_tree"]["scenario_evidence"])
        == function["decision_gate_tree"]["scenario_count"]
        for function in catalog["functions"]
    )
    html = render_after_sales_decision_gate_catalog_html(catalog)
    assert "After-Sales Decision Gate Flow" in html
    assert "YES" in html
    assert "NO" in html
    assert "Historical Scenario Evidence" in html


def test_artifact_writer_and_direct_script_entry(tmp_path) -> None:
    catalog = {
        "schema_version": "phase09-after-sales-decision-gate-catalog-v4",
        "summary": {"scenario_evidence_count": 0},
        "functions": [],
    }
    output_html = tmp_path / "catalog.html"
    output_json = tmp_path / "catalog.json"

    write_after_sales_decision_gate_catalog_artifacts(
        catalog=catalog,
        output_html=output_html,
        output_json=output_json,
    )

    assert output_html.is_file()
    assert output_json.is_file()
    assert json.loads(output_json.read_text(encoding="utf-8")) == catalog
    assert "After-Sales Decision Gate Flow" in output_html.read_text(encoding="utf-8")

    repository_root = Path(__file__).resolve().parents[2]
    script_path = (
        repository_root
        / "backend"
        / "analytics"
        / "phase09_after_sales_decision_gate_catalog.py"
    )
    result = subprocess.run(
        [sys.executable, str(script_path), "--help"],
        cwd=repository_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "after-sales decision-gate catalog" in result.stdout