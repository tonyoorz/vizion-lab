import json
from pathlib import Path

from openpyxl import load_workbook

from backend.analytics.phase09_idcevo_issue_normalization_excel import (
    NORMALIZATION_SHEET_NAMES,
    generate_phase09_idcevo_issue_normalization_excel,
    write_phase09_idcevo_issue_normalization_excel,
)
from backend.analytics.phase09_idcevo_issue_normalization import (
    build_pair_review_input_records,
)


def test_normalization_export_keeps_auditable_groups_without_changing_full_report_contract(
    tmp_path: Path,
) -> None:
    tickets = [
        {
            "ticket_id": "D-1",
            "name": "Map remains blank",
            "solution_cluster": "Navigation",
            "assigned_ecu": "IDCEVO-25",
            "closure_disposition": "Follow-up / remediation recorded",
            "risk_score": 4,
            "risk_tier": "Monitor",
            "has_follow_up_evidence": True,
            "follow_up_status": "Repair plan recorded",
            "description": "Representative original description.",
            "comments": "Representative original comment.",
        },
        {
            "ticket_id": "D-2",
            "name": "Map blank after route guidance",
            "solution_cluster": "Navigation",
            "assigned_ecu": "IDCEVO-25",
            "closure_disposition": "Direct closure without auditable rationale or follow-up",
            "risk_score": 9,
            "risk_tier": "Priority production-exposure review",
            "has_follow_up_evidence": False,
            "follow_up_status": "No follow-up evidence",
            "description": "Member evidence must not appear in representative evidence sheet.",
        },
        {
            "ticket_id": "D-3",
            "name": "Navigation color issue",
            "solution_cluster": "Navigation",
            "assigned_ecu": "IDCEVO-25",
            "closure_disposition": "Direct closure without auditable rationale or follow-up",
            "risk_score": 2,
            "risk_tier": "Monitor",
            "description": "Unreviewed singleton evidence must not appear in representative evidence sheet.",
        },
    ]
    grouping = {
        "groups": [
            {
                "group_id": "group-D-1",
                "representative_ticket_id": "D-1",
                "representative_title": "Map remains blank",
                "function_context": "navigation",
                "assigned_ecu": "IDCEVO-25",
                "normalization_status": "confirmed_duplicate_group",
                "member_ticket_ids": ["D-1", "D-2"],
                "member_count": 2,
                "merged_member_count": 1,
                "worst_risk_score": 9,
                "worst_risk_tier": "Priority production-exposure review",
                "risk_flags": ["No closure evidence"],
                "closure_disposition_counts": {
                    "Follow-up / remediation recorded": 1,
                    "Direct closure without auditable rationale or follow-up": 1,
                },
                "has_any_follow_up_evidence": True,
                "follow_up_evidence": [],
                "formal_parent_ids": ["900001"],
            },
            {
                "group_id": "group-D-3",
                "representative_ticket_id": "D-3",
                "representative_title": "Navigation color issue",
                "function_context": "navigation",
                "assigned_ecu": "IDCEVO-25",
                "normalization_status": "unmerged_singleton",
                "member_ticket_ids": ["D-3"],
                "member_count": 1,
                "merged_member_count": 0,
                "worst_risk_score": 2,
                "worst_risk_tier": "Monitor",
                "risk_flags": [],
                "closure_disposition_counts": {
                    "Direct closure without auditable rationale or follow-up": 1,
                },
                "has_any_follow_up_evidence": False,
                "follow_up_evidence": [],
                "formal_parent_ids": [],
            },
        ],
        "member_map": [
            {
                "group_id": "group-D-1",
                "ticket_id": "D-1",
                "representative_ticket_id": "D-1",
                "is_representative": True,
                "is_confirmed_member": False,
                "closure_disposition": "Follow-up / remediation recorded",
                "risk_score": 4,
                "risk_tier": "Monitor",
                "has_follow_up_evidence": True,
                "follow_up_status": "Repair plan recorded",
            },
            {
                "group_id": "group-D-1",
                "ticket_id": "D-2",
                "representative_ticket_id": "D-1",
                "is_representative": False,
                "is_confirmed_member": True,
                "closure_disposition": "Direct closure without auditable rationale or follow-up",
                "risk_score": 9,
                "risk_tier": "Priority production-exposure review",
                "has_follow_up_evidence": False,
                "follow_up_status": "No follow-up evidence",
            },
            {
                "group_id": "group-D-3",
                "ticket_id": "D-3",
                "representative_ticket_id": "D-3",
                "is_representative": True,
                "is_confirmed_member": False,
                "closure_disposition": "Direct closure without auditable rationale or follow-up",
                "risk_score": 2,
                "risk_tier": "Monitor",
                "has_follow_up_evidence": False,
                "follow_up_status": "Not recorded",
            },
        ],
        "confirmed_pairs": [
            {
                "pair_id": "pair-1",
                "input_fingerprint": "a" * 64,
                "policy_version": "phase09-duplicate-pair-v1",
                    "review_policy_version": "phase09-duplicate-pair-reviewer-v4",
                "representative_ticket_id": "D-1",
                "member_ticket_id": "D-2",
                "formal_parent_id": "900001",
                "required_evidence_ids": [
                    "formal_parent:900001",
                    "assigned_ecu:idcevo-25",
                    "function:navigation",
                ],
                "reviewer_results": [
                    {
                        "model": "deepseek-v4-pro",
                        "verdict": "SAME_UNDERLYING_DEFECT",
                        "confidence": 0.98,
                        "positive_evidence_ids": ["formal_parent:900001"],
                        "contradiction_evidence_ids": [],
                        "rationale": "Formal and technical evidence match.",
                    },
                    {
                        "model": "qwen3.7-max",
                        "verdict": "SAME_UNDERLYING_DEFECT",
                        "confidence": 0.97,
                        "positive_evidence_ids": ["formal_parent:900001"],
                        "contradiction_evidence_ids": [],
                        "rationale": "Formal and technical evidence match.",
                    },
                ],
                "reviewer_statuses": [
                    {"model": "deepseek-v4-pro", "status": "completed"},
                    {"model": "qwen3.7-max", "status": "completed"},
                ],
                "decision": "DUAL_CONFIRMED_SAME",
            }
        ],
        "quarantined_pairs": [
            {
                "pair_id": "pair-2",
                "input_fingerprint": "b" * 64,
                    "review_policy_version": "phase09-duplicate-pair-reviewer-v4",
                "representative_ticket_id": "D-1",
                "member_ticket_id": "D-3",
                "formal_parent_id": "900001",
                "required_evidence_ids": ["formal_parent:900001"],
                "reviewer_results": [],
                "reviewer_statuses": [
                    {"model": "deepseek-v4-pro", "status": "completed"},
                    {
                        "model": "qwen3.7-max",
                        "status": "request_error",
                        "error_code": "REQUEST_ERROR",
                    },
                ],
                "reason": "reviewer_non_confirmation",
            }
        ],
    }
    output_path = tmp_path / "normalization_runs" / "fixture" / "IDCEVO_Phase09_Duplicate_Normalization.xlsx"

    written_path = write_phase09_idcevo_issue_normalization_excel(
        tickets=tickets,
        grouping=grouping,
        run_manifest={"run_id": "fixture", "input_sha256": "fixture-input-sha"},
        output_path=output_path,
    )

    assert written_path == output_path
    workbook = load_workbook(output_path, data_only=True)
    try:
        assert workbook.sheetnames == list(NORMALIZATION_SHEET_NAMES)
        overview_values = {
            row[0]: row[1]
            for row in workbook["00_总览"].iter_rows(min_row=2, values_only=True)
        }
        assert overview_values["Reviewer policy"] == "phase09-duplicate-pair-reviewer-v4"
        assert overview_values["Pairs with two valid reviews"] == 1
        assert overview_values["Incomplete review pairs"] == 1
        representative_values = [
            row[0] for row in workbook["01_代表票"].iter_rows(min_row=2, values_only=True)
        ]
        assert representative_values == ["D-1", "D-3"]
        member_values = [
            row[1] for row in workbook["02_成员映射"].iter_rows(min_row=2, values_only=True)
        ]
        assert member_values == ["D-1", "D-2", "D-3"]
        confirmed_values = list(
            workbook["03_确认依据"].iter_rows(min_row=2, values_only=True)
        )
        assert confirmed_values[0][0] == "pair-1"
        assert "deepseek-v4-pro" in confirmed_values[0][7]
        assert "completed" in confirmed_values[0][8]
        assert confirmed_values[0][12] == "phase09-duplicate-pair-reviewer-v4"
        quarantined_values = list(
            workbook["04_隔离候选"].iter_rows(min_row=2, values_only=True)
        )
        assert quarantined_values[0][0] == "pair-2"
        assert "request_error" in quarantined_values[0][8]
        function_values = list(
            workbook["05_功能风险对比"].iter_rows(min_row=2, values_only=True)
        )
        assert function_values[0][1:4] == (3, 2, 1)
        representative_evidence = "\n".join(
            str(row[3] or "")
            for row in workbook["06_原始代表证据"].iter_rows(min_row=2, values_only=True)
        )
        assert "Representative original description." in representative_evidence
        assert "Member evidence must not appear" not in representative_evidence
        assert "Unreviewed singleton evidence must not appear" not in representative_evidence
    finally:
        workbook.close()


def test_normalization_generator_uses_the_frozen_source_snapshot(tmp_path: Path) -> None:
    tickets = [
        {
            "ticket_id": "D-1",
            "final_project": "IDCEVO",
            "name": "Map remains blank",
            "solution_cluster": "Navigation",
            "assigned_ecu": "IDCEVO-25",
            "effective_reason": "Child (Duplicate)",
            "parent_child_type": "Child",
            "relation_to": "D900001",
            "closure_disposition": "Follow-up / remediation recorded",
            "description": "Representative issue evidence.",
        },
        {
            "ticket_id": "D-2",
            "final_project": "IDCEVO",
            "name": "Map remains blank after route guidance",
            "solution_cluster": "Navigation",
            "assigned_ecu": "IDCEVO-25",
            "effective_reason": "Child (Duplicate)",
            "parent_child_type": "Child",
            "relation_to": "D900001",
            "closure_disposition": "Direct closure without auditable rationale or follow-up",
            "description": "Member issue evidence.",
        },
    ]
    snapshot_path = tmp_path / "source_snapshot.json"
    pair_inputs_path = tmp_path / "pair_inputs.jsonl"
    review_cache_path = tmp_path / "pair_reviews.cache.jsonl"
    snapshot_path.write_text(
        json.dumps({"source": {"scope_rule": "frozen fixture"}, "tickets": tickets}),
        encoding="utf-8",
    )
    pair_inputs_path.write_text(
        "".join(
            json.dumps(record) + "\n"
            for record in build_pair_review_input_records(tickets)
        ),
        encoding="utf-8",
    )
    review_cache_path.write_text("", encoding="utf-8")

    output_path = generate_phase09_idcevo_issue_normalization_excel(
        source_snapshot_path=snapshot_path,
        pair_inputs_path=pair_inputs_path,
        review_cache_path=review_cache_path,
        run_id="frozen-fixture",
        output_root=tmp_path / "normalization_runs",
    )

    assert output_path == (
        tmp_path
        / "normalization_runs"
        / "frozen-fixture"
        / "IDCEVO_Phase09_Duplicate_Normalization.xlsx"
    )
    assert output_path.exists()