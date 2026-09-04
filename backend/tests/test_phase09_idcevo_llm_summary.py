from __future__ import annotations

import json

from backend.analytics.phase09_idcevo_llm_summary import (
    build_phase09_llm_summary_input_records,
    write_phase09_llm_summary_input_jsonl,
)


def test_build_phase09_llm_summary_input_records_preserves_closure_context_with_bounded_source_text(
    tmp_path,
) -> None:
    full_description = (
        "Observed navigation failure with DTC 0x3B01DA and "
        "ActualValueOperatingModeDcDcConverter. === end of error description === "
        "Unrelated noisy platform inventory and another ticket symptom. "
        + "x" * 8_000
    )
    payload = {
        "tickets": [
            {
                "ticket_id": "D-RISK",
                "name": "Navigation fails after a power cycle",
                "solution_cluster": "Navigation",
                "defect_category": "",
                "product_areas": "Provide Navigation 2.0",
                "function2modul": "CD",
                "assigned_ecu": "IDCEVO-25",
                "description_full": full_description,
                "comment_records": [
                    {
                        "comment_id": "comment-1",
                        "timestamp": "2026-09-01T10:00:00Z",
                        "author": "Fixture Tester",
                        "raw_text": "Root-cause analysis is pending.",
                    }
                ],
                "audit_history_events": [
                    {
                        "timestamp": "2026-09-01T11:00:00Z",
                        "actor": "Fixture Analyst",
                        "field_name": "blocking_reason_udf",
                        "old_value": "",
                        "new_value": "Management decision",
                    }
                ],
                "effective_reason": "Management decision",
                "reason_source": "Current blocking reason",
                "rejection_route": "09 Rejected by CoC",
                "direct_close": True,
                "reopened_after_phase09": False,
                "entered_phase06_after_phase09": False,
                "follow_up_status": "No remediation or follow-up evidence",
                "target_version_value": "",
                "planning_window_value": "",
                "commercial_solution_date_value": "",
                "reprel_change_status": "Reprel change count not recorded",
                "postponement_document_status": "Template only",
                "related_resolution_status": "No explicit linked ticket",
                "risk_tier": "Priority production-exposure review",
                "risk_score": 10,
                "reporting_class": "Showstopper_Candidate",
                "problem_severity": "05-unsatisfactory",
                "severity": "Medium",
                "production_exposure_status": "Production exposure cannot be ruled out - priority review",
                "risk_flags": ["Decision/tolerance closure without repair evidence"],
                "needs_closure_evidence_review": True,
            },
            {
                "ticket_id": "D-SAFE",
                "name": "Expected behavior",
                "needs_closure_evidence_review": False,
            },
        ]
    }

    records = build_phase09_llm_summary_input_records(payload)

    assert len(records) == 1
    record = records[0]
    assert record["ticket_id"] == "D-RISK"
    assert len(record["input_fingerprint"]) == 64
    assert record["ticket"]["title"] == "Navigation fails after a power cycle"
    assert record["ticket"]["function"] == "Navigation"
    assert "Observed navigation failure with" in record["ticket"]["problem_evidence"]
    assert len(record["ticket"]["problem_evidence"]) < len(full_description)
    assert "0x3B01DA" not in record["ticket"]["problem_evidence"]
    assert "ActualValueOperatingModeDcDcConverter" not in record["ticket"]["problem_evidence"]
    assert "diagnostic code" in record["ticket"]["problem_evidence"]
    assert "Unrelated noisy platform inventory" not in record["ticket"]["problem_evidence"]
    assert "Root-cause analysis is pending." in record["ticket"]["comments_evidence"]
    assert "Fixture Tester" in record["ticket"]["comments_evidence"]
    assert "blocking_reason_udf" in record["ticket"]["history_evidence"]
    assert "Management decision" in record["ticket"]["closure_evidence"]
    assert "Showstopper_Candidate / 05-unsatisfactory" in record["ticket"]["risk_evidence"]

    output_path = tmp_path / "phase09_llm_inputs.jsonl"
    count = write_phase09_llm_summary_input_jsonl(records, output_path)
    assert count == 1
    written_record = json.loads(output_path.read_text(encoding="utf-8").strip())
    assert written_record == record