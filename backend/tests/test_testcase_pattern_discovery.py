from __future__ import annotations

import numpy as np

from backend.analytics.testcase_pattern_discovery import (
    audit_pattern_coverage,
    canonical_defect_id,
    discover_defect_patterns,
)


def test_canonical_defect_id_normalizes_octane_prefix_variants() -> None:
    assert canonical_defect_id("2113731") == "2113731"
    assert canonical_defect_id(" D2113731 ") == "2113731"
    assert canonical_defect_id("D-2113731") == "2113731"
    assert canonical_defect_id("feature-2113731") == ""


def test_discovery_groups_compatible_failure_modes_without_merging_different_ecus() -> None:
    defects = [
        {
            "defect_id": "D-1",
            "name": "RSU activation times out after reboot",
            "description": "Start remote software upgrade, reboot the vehicle, and observe the activation timeout in the RSU flow.",
            "project": "RSU",
            "assigned_ecu": "RSE",
            "lead_model": "G68",
            "software_version": "25-07",
        },
        {
            "defect_id": "2",
            "name": "RSU activation timeout during reboot",
            "description": "Start a remote software upgrade, reboot the vehicle, and observe the RSU activation timeout.",
            "project": "RSU",
            "assigned_ecu": "RSE",
            "lead_model": "G68",
            "software_version": "25-11",
        },
        {
            "defect_id": "3",
            "name": "RSU activation times out after reboot",
            "description": "Start remote software upgrade, reboot the vehicle, and observe the activation timeout in the RSU flow.",
            "project": "RSU",
            "assigned_ecu": "HU-MGU",
            "lead_model": "G68",
            "software_version": "25-07",
        },
        {
            "defect_id": "4",
            "name": "RSU issue",
            "description": "",
            "project": "RSU",
            "assigned_ecu": "RSE",
            "lead_model": "G68",
            "software_version": "25-07",
        },
    ]

    def encode(_texts: list[str]) -> np.ndarray:
        return np.array(
            [
                [1.0, 0.0],
                [0.99, 0.1],
                [1.0, 0.0],
            ],
            dtype=np.float32,
        )

    result = discover_defect_patterns(defects, encode=encode, similarity_threshold=0.8)

    assert result["summary"] == {
        "source_count": 4,
        "eligible_count": 3,
        "needs_evidence_count": 1,
        "pattern_count": 2,
        "clustered_member_count": 3,
        "singleton_pattern_count": 1,
        "estimated_llm_generations_avoided": 1,
    }
    assert [
        {member["defectId"] for member in pattern["members"]}
        for pattern in result["patterns"]
    ] == [{"1", "2"}, {"3"}]
    assert result["needs_evidence"] == [
        {
            "defectId": "4",
            "name": "RSU issue",
            "reason": "missing_reproduction_evidence",
        }
    ]


def test_discovery_requires_stronger_similarity_when_execution_context_is_unknown() -> None:
    defects = [
        {
            "defect_id": "1",
            "name": "RSU activation times out after reboot",
            "description": "Start remote software upgrade, reboot the vehicle, and observe the activation timeout in the RSU flow.",
            "project": "RSU",
        },
        {
            "defect_id": "2",
            "name": "RSU activation timeout during reboot",
            "description": "Start a remote software upgrade, reboot the vehicle, and observe the RSU activation timeout.",
            "project": "RSU",
        },
    ]

    result = discover_defect_patterns(
        defects,
        encode=lambda _texts: np.array([[1.0, 0.0], [0.86, 0.51]], dtype=np.float32),
        similarity_threshold=0.84,
    )

    assert [len(pattern["members"]) for pattern in result["patterns"]] == [1, 1]
    assert all(pattern["requiresManualReview"] is True for pattern in result["patterns"])


def test_discovery_keeps_the_central_representative_for_similarity_evidence() -> None:
    defects = [
        {
            "defect_id": "1",
            "name": "RSU activation behavior A",
            "description": "Start remote software upgrade and observe the activation behavior after reboot.",
            "project": "RSU",
            "assigned_ecu": "RSE",
            "lead_model": "G68",
        },
        {
            "defect_id": "2",
            "name": "RSU activation behavior B",
            "description": "Start remote software upgrade and observe the activation behavior after reboot.",
            "project": "RSU",
            "assigned_ecu": "RSE",
            "lead_model": "G68",
        },
        {
            "defect_id": "3",
            "name": "RSU activation behavior C",
            "description": "Start remote software upgrade and observe the activation behavior after reboot.",
            "project": "RSU",
            "assigned_ecu": "RSE",
            "lead_model": "G68",
        },
    ]

    result = discover_defect_patterns(
        defects,
        encode=lambda _texts: np.array(
            [[1.0, 0.0], [0.9, 0.4359], [0.6, 0.8]],
            dtype=np.float32,
        ),
        similarity_threshold=0.8,
    )

    pattern = result["patterns"][0]
    assert pattern["representative"]["defectId"] == "2"
    assert [member["defectId"] for member in pattern["members"]] == ["2", "1", "3"]
    assert all(member["similarityToRepresentative"] >= 0.8 for member in pattern["members"])


def test_discovery_does_not_merge_unrelated_failure_modes_in_the_same_context() -> None:
    defects = [
        {
            "defect_id": "1",
            "name": "RSU activation timeout after reboot",
            "description": "Start remote software upgrade, reboot the vehicle, and observe activation timing out.",
            "project": "RSU",
            "assigned_ecu": "RSE",
            "lead_model": "G68",
        },
        {
            "defect_id": "2",
            "name": "RSU activation timeout during reboot",
            "description": "Start remote software upgrade, reboot the vehicle, and observe the activation timeout.",
            "project": "RSU",
            "assigned_ecu": "RSE",
            "lead_model": "G68",
        },
        {
            "defect_id": "3",
            "name": "RSU media widget overlaps the navigation panel",
            "description": "Open the media widget and observe it overlapping the navigation panel on the display.",
            "project": "RSU",
            "assigned_ecu": "RSE",
            "lead_model": "G68",
        },
    ]

    result = discover_defect_patterns(
        defects,
        encode=lambda _texts: np.array(
            [[1.0, 0.0], [0.99, 0.1], [0.99, 0.1]],
            dtype=np.float32,
        ),
        similarity_threshold=0.8,
    )

    assert [
        {member["defectId"] for member in pattern["members"]}
        for pattern in result["patterns"]
    ] == [{"1", "2"}, {"3"}]
    assert result["patterns"][0]["commonAnchorTerms"] == ["activation", "reboot"]


def test_discovery_flags_oversized_patterns_for_manual_review() -> None:
    defects = [
        {
            "defect_id": str(index),
            "name": "RSU activation timeout after reboot",
            "description": "Start remote software upgrade, reboot the vehicle, and observe activation timing out.",
            "project": "RSU",
            "assigned_ecu": "RSE",
            "lead_model": "G68",
        }
        for index in range(1, 22)
    ]

    result = discover_defect_patterns(
        defects,
        encode=lambda _texts: np.tile(np.array([[1.0, 0.0]], dtype=np.float32), (21, 1)),
        similarity_threshold=0.8,
    )

    assert len(result["patterns"]) == 1
    assert result["patterns"][0]["requiresManualReview"] is True


def test_coverage_audit_keeps_exact_links_and_semantic_matches_advisory() -> None:
    patterns = [
        {
            "patternId": "pattern-1",
            "requiresManualReview": False,
            "representative": {
                "defectId": "1",
                "name": "RSU activation timeout after reboot",
                "project": "RSU",
                "assignedEcu": "RSE",
                "leadModel": "G68",
            },
            "members": [
                {"defectId": "1", "name": "RSU activation timeout after reboot"},
                {"defectId": "D-2", "name": "RSU activation timeout during reboot"},
            ],
        },
        {
            "patternId": "pattern-2",
            "requiresManualReview": True,
            "representative": {
                "defectId": "3",
                "name": "RSU media widget overlap",
                "project": "RSU",
                "assignedEcu": "RSE",
                "leadModel": "G68",
            },
            "members": [{"defectId": "3", "name": "RSU media widget overlap"}],
        },
    ]
    queried_representatives: list[str] = []

    result = audit_pattern_coverage(
        patterns,
        direct_testcases_by_defect={
            "2": [{"testId": "900", "name": "Existing RSU reboot regression", "source": "octane_testcases"}],
        },
        retrieve_similar=lambda representative: (
            queried_representatives.append(representative["defectId"])
            or [{"testId": "901", "name": "Similar RSU activation regression", "score": 0.88, "team": "DTSV_China"}]
        ),
    )

    assert queried_representatives == ["1", "3"]
    assert result["summary"] == {
        "audited_pattern_count": 2,
        "direct_linked_member_count": 1,
        "semantic_candidate_pattern_count": 2,
        "review_existing_testcase_count": 1,
        "review_generate_reusable_testcase_count": 0,
        "requires_manual_review_count": 1,
    }
    first_audit, second_audit = result["audits"]
    assert first_audit["recommendedDisposition"] == "review_existing_testcase"
    assert first_audit["directLinks"] == [{
        "defectId": "2",
        "testcases": [{
            "testId": "900",
            "name": "Existing RSU reboot regression",
            "source": "direct",
        }],
    }]
    assert first_audit["semanticCandidates"] == [{
        "testId": "901",
        "name": "Similar RSU activation regression",
        "score": 0.88,
        "team": "DTSV_China",
        "source": "semantic",
    }]
    assert second_audit["recommendedDisposition"] == "requires_manual_review"