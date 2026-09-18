from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import pytest

from backend.analytics.phase09_idcevo_issue_normalization import (
    build_confirmed_duplicate_groups,
    build_pair_review_input_records,
    build_representative_anchored_review_pairs,
    build_strict_duplicate_candidates,
    write_frozen_pair_review_inputs,
)


def _ticket(
    ticket_id: str,
    *,
    title: str,
    assigned_ecu: str = "IDCEVO-25",
    solution_cluster: str = "Navigation",
    effective_reason: str = "Child (Duplicate)",
    parent_child_type: str = "Child",
    relation_to: str = "D900001",
    description: str = "Observed behaviour: map remains blank. Preconditions: start navigation.",
) -> dict[str, object]:
    return {
        "ticket_id": ticket_id,
        "final_project": "IDCEVO",
        "legacy_project": "IDCEVO",
        "name": title,
        "assigned_ecu": assigned_ecu,
        "solution_cluster": solution_cluster,
        "effective_reason": effective_reason,
        "parent_child_type": parent_child_type,
        "relation_to": relation_to,
        "description": description,
        "comments": "",
        "software_version": "BMW IDCEVO;2607_i460-25w50.2-1;idcevo",
        "closure_disposition": "Direct closure without auditable rationale or follow-up",
    }


def test_strict_candidate_allows_verified_shared_duplicate_parent_with_compatible_context() -> None:
    candidates = build_strict_duplicate_candidates(
        [
            _ticket("D-1", title="Navigation map remains blank"),
            _ticket("D-2", title="Map remains blank after navigation starts"),
            _ticket(
                "D-3",
                title="Navigation map remains blank",
                assigned_ecu="CDE-01",
            ),
        ]
    )

    by_pair = {
        (candidate["left_ticket_id"], candidate["right_ticket_id"]): candidate
        for candidate in candidates
    }

    matching = by_pair[("D-1", "D-2")]
    assert matching["eligible_for_llm_review"] is True
    assert matching["candidate_channels"] == ["shared_verified_duplicate_parent"]
    assert matching["shared_duplicate_parent_ids"] == ["900001"]
    assert matching["hard_contradictions"] == []

    conflicting = by_pair[("D-1", "D-3")]
    assert conflicting["eligible_for_llm_review"] is False
    assert "assigned_ecu_conflict" in conflicting["hard_contradictions"]


def test_exact_title_without_a_second_identity_anchor_never_enters_llm_merge_review() -> None:
    candidates = build_strict_duplicate_candidates(
        [
            _ticket(
                "D-1",
                title="Black screen on startup",
                effective_reason="Not reproducible",
                parent_child_type="",
                relation_to="",
                description="Observed behaviour: black screen after a cold start.",
            ),
            _ticket(
                "D-2",
                title="Black screen on startup",
                effective_reason="Not reproducible",
                parent_child_type="",
                relation_to="",
                description="Observed behaviour: black screen after a warm restart.",
            ),
        ]
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["candidate_channels"] == ["exact_normalized_title_and_context"]
    assert candidate["eligible_for_llm_review"] is False
    assert candidate["rejection_reason"] == "title_context_match_without_second_identity_anchor"


def test_representative_anchored_review_avoids_transitive_all_pairs_and_prefers_richer_evidence() -> None:
    tickets = [
        _ticket(
            "D-1",
            title="Map remains blank",
            description="Observed behaviour: map remains blank.",
        ),
        _ticket(
            "D-2",
            title="Map remains blank after navigation starts",
            description=(
                "Observed behaviour: map remains blank after navigation starts. "
                "Preconditions: start navigation with route guidance. "
                "Reproduction: repeat after a vehicle restart."
            ),
        ),
        _ticket(
            "D-3",
            title="Navigation map blank",
            description="Observed behaviour: map remains blank after navigation starts.",
        ),
    ]

    review_pairs = build_representative_anchored_review_pairs(tickets)

    assert [
        (pair["left_ticket_id"], pair["right_ticket_id"])
        for pair in review_pairs
    ] == [("D-1", "D-2"), ("D-2", "D-3")]
    assert {pair["representative_ticket_id"] for pair in review_pairs} == {"D-2"}
    assert all(pair["eligible_for_llm_review"] is True for pair in review_pairs)


def test_pair_review_input_is_bounded_and_cites_required_hard_evidence() -> None:
    tickets = [
        _ticket(
            "D-1",
            title="Map remains blank",
            description=(
                "<p>Observed behaviour: map remains blank after navigation starts.</p>"
                "<p>Preconditions: start route guidance.</p>"
                "<p>=== end of error description === unrelated platform inventory</p>"
            ),
        ),
        _ticket(
            "D-2",
            title="Navigation map blank",
            description="Observed behaviour: map remains blank after navigation starts.",
        ),
    ]
    tickets[0]["comment_records"] = [
        {
            "timestamp": "2026-01-01T00:00:00Z",
            "author": "Engineer A",
            "raw_text": "<p>Same root cause as parent D900001.</p>",
        }
    ]
    tickets[0]["audit_history_events"] = [
        {
            "timestamp": "2026-01-01T01:00:00Z",
            "actor": "Engineer A",
            "field_name": "blocking_reason_udf",
            "old_value": "",
            "new_value": "Child (Duplicate)",
        }
    ]

    records = build_pair_review_input_records(tickets)

    assert len(records) == 1
    record = records[0]
    assert record["representative_ticket_id"] == "D-1"
    assert len(record["input_fingerprint"]) == 64
    assert record["required_evidence_ids"] == [
        "formal_parent:900001",
        "assigned_ecu:idcevo-25",
        "function:navigation",
    ]
    evidence_ids = {item["evidence_id"] for item in record["evidence"]}
    assert evidence_ids >= set(
        record["required_evidence_ids"]
    )
    assert {
        "ticket_a_title",
        "ticket_a_issue_evidence",
        "ticket_a_process_evidence",
        "ticket_b_title",
        "ticket_b_issue_evidence",
        "ticket_b_process_evidence",
    } <= evidence_ids
    assert "map remains blank" in record["left"]["issue_evidence"].casefold()
    assert "unrelated platform inventory" not in record["left"]["issue_evidence"].casefold()
    assert "<p>" not in record["left"]["issue_evidence"].casefold()
    assert "Engineer A" in record["left"]["process_evidence"]
    assert "blocking_reason_udf" in record["left"]["process_evidence"]


def _completed_review(
    pair_input: dict[str, object],
    model: str,
    verdict: str,
    *,
    confidence: float = 0.98,
) -> dict[str, object]:
    outcome: dict[str, object] = {
        "verdict": verdict,
        "confidence": confidence,
        "positive_evidence_ids": [],
        "contradiction_evidence_ids": [],
        "rationale": "Fixture pair review.",
    }
    if verdict == "SAME_UNDERLYING_DEFECT":
        outcome["positive_evidence_ids"] = [
            *pair_input["required_evidence_ids"],
            "ticket_a_issue_evidence",
        ]
        outcome["verified_required_evidence_ids"] = pair_input[
            "required_evidence_ids"
        ]
    return {
        "pair_id": pair_input["pair_id"],
        "input_fingerprint": pair_input["input_fingerprint"],
        "policy_version": pair_input["policy_version"],
        "review_policy_version": "phase09-duplicate-pair-reviewer-v4",
        "model": model,
        "status": "completed",
        "outcome": outcome,
    }


def test_confirmed_groups_require_two_same_reviews_against_the_representative() -> None:
    tickets = [
        _ticket(
            "D-1",
            title="Map remains blank",
            description=(
                "Observed behaviour: map remains blank after navigation starts. "
                "Preconditions: start route guidance. Reproduction: restart vehicle."
            ),
        ),
        _ticket(
            "D-2",
            title="Map remains blank after route guidance",
            description="Observed behaviour: map remains blank after navigation starts.",
        ),
        _ticket(
            "D-3",
            title="Navigation map has an unrelated color issue",
            description="Observed behaviour: map color is incorrect.",
        ),
    ]
    tickets[0].update(
        {
            "closure_disposition": "Follow-up / remediation recorded",
            "risk_score": 4,
            "risk_tier": "Monitor",
            "has_follow_up_evidence": True,
            "follow_up_status": "Repair plan recorded",
        }
    )
    tickets[1].update(
        {
            "risk_score": 9,
            "risk_tier": "Priority production-exposure review",
            "has_follow_up_evidence": False,
            "follow_up_status": "No follow-up evidence",
        }
    )
    tickets[2].update({"risk_score": 2, "risk_tier": "Monitor"})
    pair_inputs = build_pair_review_input_records(tickets)
    confirmed_pair = next(
        record for record in pair_inputs if "D-2" in {record["left_ticket_id"], record["right_ticket_id"]}
    )
    quarantined_pair = next(
        record for record in pair_inputs if "D-3" in {record["left_ticket_id"], record["right_ticket_id"]}
    )
    review_records = [
        _completed_review(confirmed_pair, "deepseek-v4-pro", "SAME_UNDERLYING_DEFECT"),
        _completed_review(confirmed_pair, "qwen3.7-max", "SAME_UNDERLYING_DEFECT"),
        _completed_review(quarantined_pair, "deepseek-v4-pro", "SAME_UNDERLYING_DEFECT"),
        _completed_review(quarantined_pair, "qwen3.7-max", "DISTINCT"),
    ]

    grouped = build_confirmed_duplicate_groups(tickets, pair_inputs, review_records)

    groups_by_representative = {
        group["representative_ticket_id"]: group for group in grouped["groups"]
    }
    confirmed_group = groups_by_representative[confirmed_pair["representative_ticket_id"]]
    assert confirmed_group["member_ticket_ids"] == ["D-1", "D-2"]
    assert confirmed_group["member_count"] == 2
    assert confirmed_group["worst_risk_score"] == 9
    assert confirmed_group["closure_disposition_counts"] == {
        "Direct closure without auditable rationale or follow-up": 1,
        "Follow-up / remediation recorded": 1,
    }
    assert confirmed_group["follow_up_evidence"] == [
        {
            "ticket_id": "D-1",
            "has_follow_up_evidence": True,
            "follow_up_status": "Repair plan recorded",
        },
        {
            "ticket_id": "D-2",
            "has_follow_up_evidence": False,
            "follow_up_status": "No follow-up evidence",
        },
    ]
    assert groups_by_representative["D-3"]["member_ticket_ids"] == ["D-3"]
    assert grouped["confirmed_pairs"][0]["pair_id"] == confirmed_pair["pair_id"]
    assert (
        grouped["confirmed_pairs"][0]["review_policy_version"]
        == "phase09-duplicate-pair-reviewer-v4"
    )
    assert grouped["quarantined_pairs"][0]["pair_id"] == quarantined_pair["pair_id"]
    assert grouped["quarantined_pairs"][0]["reason"] == "reviewer_non_confirmation"


def test_confirmed_groups_reject_same_verdicts_without_program_verified_hard_evidence() -> None:
    tickets = [
        _ticket("D-1", title="Map remains blank"),
        _ticket("D-2", title="Map remains blank after route guidance"),
    ]
    pair_input = build_pair_review_input_records(tickets)[0]
    qwen_review = _completed_review(
        pair_input, "qwen3.7-max", "SAME_UNDERLYING_DEFECT"
    )
    qwen_review["outcome"].pop("verified_required_evidence_ids")

    grouped = build_confirmed_duplicate_groups(
        tickets,
        [pair_input],
        [
            _completed_review(
                pair_input, "deepseek-v4-pro", "SAME_UNDERLYING_DEFECT"
            ),
            qwen_review,
        ],
    )

    assert all(group["member_count"] == 1 for group in grouped["groups"])
    assert grouped["confirmed_pairs"] == []
    assert grouped["quarantined_pairs"][0]["reason"] == "missing_or_invalid_reviewer_result"
    assert grouped["quarantined_pairs"][0]["reviewer_statuses"] == [
        {"model": "deepseek-v4-pro", "status": "completed"},
        {"model": "qwen3.7-max", "status": "invalid_completed_record"},
    ]


def test_unreviewed_pairs_are_kept_separate_from_reviewer_non_confirmation() -> None:
    tickets = [
        _ticket("D-1", title="Map remains blank"),
        _ticket("D-2", title="Map remains blank after route guidance"),
    ]
    pair_input = build_pair_review_input_records(tickets)[0]

    grouped = build_confirmed_duplicate_groups(tickets, [pair_input], [])

    assert grouped["confirmed_pairs"] == []
    assert grouped["quarantined_pairs"] == [
        {
            "pair_id": pair_input["pair_id"],
            "input_fingerprint": pair_input["input_fingerprint"],
            "policy_version": pair_input["policy_version"],
            "review_policy_version": "phase09-duplicate-pair-reviewer-v4",
            "representative_ticket_id": "D-1",
            "member_ticket_id": "D-2",
            "formal_parent_id": "900001",
            "required_evidence_ids": pair_input["required_evidence_ids"],
            "reviewer_results": [],
            "reviewer_statuses": [
                {"model": "deepseek-v4-pro", "status": "not_run"},
                {"model": "qwen3.7-max", "status": "not_run"},
            ],
            "reason": "missing_or_invalid_reviewer_result",
        }
    ]


def test_confirmed_groups_require_the_current_reviewer_policy_version() -> None:
    tickets = [
        _ticket("D-1", title="Map remains blank"),
        _ticket("D-2", title="Map remains blank after route guidance"),
    ]
    pair_input = build_pair_review_input_records(tickets)[0]
    qwen_review = _completed_review(
        pair_input, "qwen3.7-max", "SAME_UNDERLYING_DEFECT"
    )
    qwen_review["review_policy_version"] = "phase09-duplicate-pair-reviewer-v3"

    grouped = build_confirmed_duplicate_groups(
        tickets,
        [pair_input],
        [
            _completed_review(
                pair_input, "deepseek-v4-pro", "SAME_UNDERLYING_DEFECT"
            ),
            qwen_review,
        ],
    )

    assert grouped["confirmed_pairs"] == []
    assert grouped["quarantined_pairs"][0]["reason"] == "missing_or_invalid_reviewer_result"


def test_frozen_pair_inputs_use_the_raw_file_byte_hash_on_windows(tmp_path: Path) -> None:
    payload = {
        "source": {"scope_rule": "final tproject fixture"},
        "tickets": [
            _ticket("D-1", title="Map remains blank"),
            _ticket("D-2", title="Map remains blank after route guidance"),
        ],
    }
    run_directory = tmp_path / "normalization-run"

    manifest = write_frozen_pair_review_inputs(
        payload=payload,
        run_directory=run_directory,
        generated_at="2026-09-08T00:00:00Z",
    )

    pair_input_bytes = (run_directory / "pair_inputs.jsonl").read_bytes()
    snapshot_bytes = (run_directory / "source_snapshot.json").read_bytes()
    saved_manifest = json.loads((run_directory / "source_manifest.json").read_text(encoding="utf-8"))
    assert manifest == saved_manifest
    assert manifest["target_ticket_count"] == 2
    assert manifest["pair_input_count"] == 1
    assert manifest["pair_inputs_sha256"] == sha256(pair_input_bytes).hexdigest()
    assert manifest["source_snapshot_sha256"] == sha256(snapshot_bytes).hexdigest()


def test_frozen_pair_inputs_reject_a_payload_without_a_ticket_array(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="tickets array"):
        write_frozen_pair_review_inputs(
            payload={"tickets": "not a list"},
            run_directory=tmp_path / "invalid-run",
        )