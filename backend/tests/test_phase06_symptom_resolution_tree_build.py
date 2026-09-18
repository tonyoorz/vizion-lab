import json
import subprocess
import sys
from pathlib import Path

from openpyxl import load_workbook

from backend.analytics.phase06_symptom_resolution_tree_build import (
    build_phase06_symptom_resolution_trees,
    build_phase06_tree_records,
    export_phase06_symptom_resolution_tree_catalog,
)


PHASE06_AUDIT_RECORD = {
    "ticket_id": "D-1",
    "function": "CarPlay",
    "solution_cluster": "CarPlay",
    "defect_category": "Connectivity",
    "ticket_title": "Apple CarPlay connection fails",
    "problem_description_excerpt": "Connection fails while pairing an iPhone through USB.",
    "repair_plan_evidence": "Repair plan evidence excerpt",
    "decision_evidence": "",
    "no_fix_evidence": "",
    "evidence_index": "D-1: Ticket description; Ticket comment",
    "review_priority": "P1",
    "candidate_status": "llm_causal_signal_with_recorded_remedy_evidence",
    "llm_root_cause_assessment": "LLM root cause must not become a tree solution.",
    "llm_fix_quality_assessment": "LLM fix assessment must not become a tree solution.",
    "approved_tree_rule": False,
}


def test_build_phase06_tree_records_uses_recorded_resolution_evidence_only() -> None:
    records = build_phase06_tree_records([PHASE06_AUDIT_RECORD])

    assert len(records) == 1
    assert records[0]["scenario_id"] == "D-1"
    assert records[0]["customer_symptom"] == "Apple CarPlay connection fails"
    assert records[0]["trigger_condition"] == (
        "Connection fails while pairing an iPhone through USB."
    )
    assert records[0]["recorded_resolution_evidence"] == "Repair plan evidence excerpt"
    assert records[0]["recorded_resolution_source"] == "Repair plan evidence"
    assert records[0]["rca_review_priority"] == "P1"
    assert "recovery_action" not in records[0]
    assert "llm_root_cause_assessment" not in records[0]
    assert "LLM root cause" not in records[0]["recorded_resolution_evidence"]


def test_build_phase06_symptom_resolution_trees_has_v12_layers_without_rca_rules() -> None:
    trees, nodes = build_phase06_symptom_resolution_trees(
        build_phase06_tree_records([PHASE06_AUDIT_RECORD])
    )

    carplay_tree = next(tree for tree in trees if tree["tree_id"] == "DT-CARPLAY")
    carplay_nodes = [node for node in nodes if node["tree_id"] == "DT-CARPLAY"]
    assert carplay_tree["tree_generation"] == (
        "phase06_ticket_symptom_resolution_evidence_v1"
    )
    assert carplay_tree["p1_review_ticket_count"] == 1
    assert carplay_tree["p2_review_ticket_count"] == 0
    assert {node["node_type"] for node in carplay_nodes} == {
        "Entry",
        "Issue Group",
        "Symptom Pattern",
        "Trigger Pattern",
        "Recorded Resolution Evidence",
    }
    resolution = next(
        node
        for node in carplay_nodes
        if node["node_type"] == "Recorded Resolution Evidence"
    )
    assert resolution["resolution_status"] == (
        "ticket_recorded_resolution_evidence"
    )
    assert "Repair plan evidence excerpt" in resolution["question_or_action"]
    assert resolution["p1_review_ticket_count"] == 1
    assert resolution["p2_review_ticket_count"] == 0
    assert resolution["approved_tree_rule"] is False
    assert all(node["approved_tree_rule"] is False for node in nodes)
    assert "LLM root cause" not in resolution["question_or_action"]


def test_solution_leaf_uses_its_own_issue_group_review_metadata() -> None:
    second_record = {
        **PHASE06_AUDIT_RECORD,
        "ticket_id": "D-2",
        "ticket_title": "Apple CarPlay black screen",
        "problem_description_excerpt": "Projection display remains black during playback.",
        "repair_plan_evidence": "",
        "decision_evidence": "Decision evidence excerpt",
        "evidence_index": "D-2: Ticket description; Ticket comment",
        "review_priority": "P2",
    }
    _, nodes = build_phase06_symptom_resolution_trees(
        build_phase06_tree_records([PHASE06_AUDIT_RECORD, second_record])
    )

    second_solution = next(
        node
        for node in nodes
        if node["tree_id"] == "DT-CARPLAY" and node["node_id"] == "SOL-02-01"
    )
    assert second_solution["p1_review_ticket_count"] == 0
    assert second_solution["p2_review_ticket_count"] == 1
    assert second_solution["source_ticket_ids"] == "D-2"


def test_phase06_no_recovery_wording_does_not_create_resolution_evidence() -> None:
    no_recovery_record = {
        **PHASE06_AUDIT_RECORD,
        "repair_plan_evidence": "No recovery recorded.",
        "decision_evidence": "",
    }
    records = build_phase06_tree_records([no_recovery_record])
    _, nodes = build_phase06_symptom_resolution_trees(records)

    resolution = next(
        node
        for node in nodes
        if node["tree_id"] == "DT-CARPLAY"
        and node["node_type"] == "Recorded Resolution Evidence"
    )
    assert records[0]["recorded_resolution_evidence"] == ""
    assert resolution["question_or_action"] == ""
    assert resolution["resolution_status"] == "no_recorded_resolution_evidence"
    assert resolution["recorded_resolution_evidence_ticket_count"] == 0


def test_phase06_uses_decision_evidence_when_repair_plan_is_unusable() -> None:
    record = {
        **PHASE06_AUDIT_RECORD,
        "repair_plan_evidence": "(blank)",
        "decision_evidence": "Decision evidence excerpt",
    }

    tree_record = build_phase06_tree_records([record])[0]

    assert tree_record["recorded_resolution_evidence"] == "Decision evidence excerpt"
    assert tree_record["recorded_resolution_source"] == "Decision evidence"


def test_export_phase06_tree_catalog_writes_v12_shaped_excel_json_and_html(
    tmp_path: Path,
) -> None:
    trees, nodes = build_phase06_symptom_resolution_trees(
        build_phase06_tree_records([PHASE06_AUDIT_RECORD])
    )
    catalog = {
        "source_workbook_name": "Phase06full.xlsx",
        "summary": {
            "all_ticket_count": 1,
            "validated_llm_summary_count": 1,
            "p1_candidate_count": 1,
            "p2_candidate_count": 0,
            "approved_tree_rule_count": 0,
        },
        "tree_records": build_phase06_tree_records([PHASE06_AUDIT_RECORD]),
        "review_queue": [PHASE06_AUDIT_RECORD],
        "decision_trees": trees,
        "decision_tree_nodes": nodes,
    }
    output_xlsx = tmp_path / "phase06-tree.xlsx"
    output_json = tmp_path / "phase06-tree.json"
    output_html = tmp_path / "phase06-tree.html"

    export_phase06_symptom_resolution_tree_catalog(
        catalog,
        output_xlsx=output_xlsx,
        output_json=output_json,
        output_html=output_html,
    )

    workbook = load_workbook(output_xlsx, data_only=True)
    try:
        assert workbook.sheetnames == [
            "00_Overview",
            "01_Phase06_Ticket_Library",
            "02_RCA_Review_Queue",
            "03_Service_Function_Catalog",
            "04_Decision_Tree_Catalog",
            "05_Decision_Tree_Nodes",
            "06_Source_Contract",
        ]
        node_headers = next(
            workbook["05_Decision_Tree_Nodes"].iter_rows(
                min_row=1, max_row=1, values_only=True
            )
        )
        assert {
            "Recorded Resolution Evidence Count",
            "P1 Review Ticket Count",
            "P2 Review Ticket Count",
            "No-Fix Evidence Ticket Count",
            "Approved Tree Rule",
        }.issubset(node_headers)
    finally:
        workbook.close()

    payload = json.loads(output_json.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "phase06-symptom-resolution-tree-kb-v1"
    assert payload["policy"]["automatic_tree_rule_count"] == 0
    assert all(
        node["approved_tree_rule"] is False
        for node in payload["decision_tree_nodes"]
    )
    assert all(
        "reference_tree_ids" not in tree and "solution_source_policy" not in tree
        for tree in payload["decision_trees"]
    )
    html = output_html.read_text(encoding="utf-8")
    assert "Phase 06 Symptom-to-Resolution Evidence Tree Catalog" in html
    assert "Recorded Resolution Evidence" in html
    assert "No automatic RCA or repair rules" in html
    assert "Ticket Solution" not in html
    assert "DTREF-" not in html
    assert "decision_tree_sources" not in html
    assert "source_asset" not in html
    assert "Ticket Recovery Action" not in html
    assert 'node.node_type === "Recorded Resolution Evidence"' in html
    assert '"No recorded resolution evidence"' in html
    assert "Recorded resolution evidence: " in html
    assert " * 156 }; }" in html
    assert ".tree-meta div:last-child { grid-column: span 2; }" in html


def test_tree_builder_cli_runs_when_executed_as_a_script() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            str(
                repo_root
                / "backend"
                / "analytics"
                / "phase06_symptom_resolution_tree_build.py"
            ),
            "--help",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--workbook" in result.stdout


def test_tree_builder_cli_runs_as_a_module() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "backend.analytics.phase06_symptom_resolution_tree_build",
            "--help",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--output-html" in result.stdout