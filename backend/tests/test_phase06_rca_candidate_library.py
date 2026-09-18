import json
from pathlib import Path

from openpyxl import Workbook, load_workbook

from backend.analytics.phase06_rca_candidate_library import (
    build_phase06_rca_candidate_library_from_sources,
    build_phase06_rca_candidate_library,
    build_rca_candidate_record,
    export_phase06_rca_candidate_library,
    load_validated_phase06_summaries,
    parse_phase06_summary,
)


ROOT_CAUSE_LABEL = "\u6839\u56e0\uff1a"
FIX_QUALITY_LABEL = "\u4fee\u590d\u8d28\u91cf\uff1a"
CLOSURE_LABEL = "Reject/\u5173\u95ed\u539f\u56e0\uff1a"
FOLLOW_UP_LABEL = "\u540e\u7eed\u95ed\u73af\uff1a"
RISK_LABEL = "\u98ce\u9669\u5224\u65ad\u4e0e\u9003\u9038\u56de\u987e\uff1a"


def _valid_summary(
    root_cause: str = "\u7531\u4e8e\u72b6\u6001\u540c\u6b65\u5931\u8d25\uff0c\u5bfc\u81f4\u8fde\u63a5\u4e2d\u65ad\u3002",
    fix_quality: str = "\u5df2\u5b8c\u6210\u4fee\u590d\u5e76\u901a\u8fc7\u56de\u5f52\u9a8c\u8bc1\u3002",
) -> str:
    return "\n".join(
        (
            f"{ROOT_CAUSE_LABEL}{root_cause}",
            f"{FIX_QUALITY_LABEL}{fix_quality}",
            f"{CLOSURE_LABEL}\u6b63\u5e38\u5173\u95ed\u3002",
            f"{FOLLOW_UP_LABEL}\u5df2\u8bb0\u5f55\u4fee\u590d\u8ba1\u5212\u3002",
            f"{RISK_LABEL}\u65e0\u989d\u5916\u98ce\u9669\u8bc1\u636e\u3002",
        )
    )


def _write_jsonl(path: Path, records: list[dict]) -> Path:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    return path


def _detail_record(
    *,
    repair_plan_evidence: str = "\u4fee\u590d\u4ee3\u7801\u5df2\u53d1\u5e03\u3002",
    decision_evidence: str = "",
    no_fix_evidence: str = "",
) -> dict[str, str]:
    return {
        "Ticket ID": "D-1",
        "\u529f\u80fd": "Phone Projection",
        "Solution Cluster": "Projection",
        "Defect Category": "Connectivity",
        "Assigned ECU": "HU-H",
        "Software Version": "24-03",
        "\u5173\u95ed\u5904\u7f6e": "Resolution with follow-up needed",
        "Ticket \u6807\u9898": "Projection connection fails",
        "\u95ee\u9898\u63cf\u8ff0\u6458\u5f55": "Connection stops after device pairing.",
        "\u8bc1\u636e\u539f\u6587\u7d22\u5f15": "D-1: Ticket description; Ticket comment",
        "\u4fee\u590d\u8ba1\u5212\u8bc1\u636e": repair_plan_evidence,
        "\u51b3\u7b56\u8bc1\u636e": decision_evidence,
        "\u65e0\u4fee\u590d\u8bc1\u636e": no_fix_evidence,
    }


def _validated_summary(
    root_cause: str,
    fix_quality: str = "\u5df2\u5b8c\u6210\u4fee\u590d\u5e76\u901a\u8fc7\u56de\u5f52\u9a8c\u8bc1\u3002",
) -> dict[str, str]:
    return {
        "ticket_id": "D-1",
        "input_fingerprint": "source-a",
        "input_version": "phase06-llm-summary-v1",
        "model": "test-model",
        "source": "llm",
        "summary": _valid_summary(root_cause, fix_quality),
        **parse_phase06_summary(_valid_summary(root_cause, fix_quality)),
    }


def test_load_validated_summaries_requires_matching_input_fingerprint(
    tmp_path: Path,
) -> None:
    inputs_path = _write_jsonl(
        tmp_path / "inputs.jsonl",
        [
            {
                "ticket_id": "D-1",
                "input_fingerprint": "source-a",
                "input_version": "phase06-llm-summary-v1",
                "ticket": {},
            }
        ],
    )
    summaries_path = _write_jsonl(
        tmp_path / "summaries.jsonl",
        [
            {
                "ticket_id": "D-1",
                "input_fingerprint": "other-source",
                "input_version": "phase06-llm-summary-v1",
                "source": "llm",
                "model": "test-model",
                "summary": _valid_summary(),
            }
        ],
    )

    validated, audit = load_validated_phase06_summaries(inputs_path, summaries_path)

    assert validated == {}
    assert audit["input_record_count"] == 1
    assert audit["unmatched_or_invalid_summary_count"] == 1


def test_load_validated_summaries_rejects_ambiguous_duplicate_input_ticket(
    tmp_path: Path,
) -> None:
    inputs_path = _write_jsonl(
        tmp_path / "inputs.jsonl",
        [
            {
                "ticket_id": "D-1",
                "input_fingerprint": "source-a",
                "input_version": "phase06-llm-summary-v1",
                "ticket": {},
            },
            {
                "ticket_id": "D-1",
                "input_fingerprint": "source-b",
                "input_version": "phase06-llm-summary-v1",
                "ticket": {},
            },
        ],
    )
    summaries_path = _write_jsonl(
        tmp_path / "summaries.jsonl",
        [
            {
                "ticket_id": "D-1",
                "input_fingerprint": "source-a",
                "input_version": "phase06-llm-summary-v1",
                "source": "llm",
                "model": "test-model",
                "summary": _valid_summary(),
            }
        ],
    )

    validated, audit = load_validated_phase06_summaries(inputs_path, summaries_path)

    assert validated == {}
    assert audit["input_duplicate_count"] == 1
    assert audit["ambiguous_input_ticket_count"] == 1
    assert audit["ambiguous_input_summary_count"] == 1


def test_load_validated_summaries_requires_llm_summary_source(tmp_path: Path) -> None:
    inputs_path = _write_jsonl(
        tmp_path / "inputs.jsonl",
        [
            {
                "ticket_id": "D-1",
                "input_fingerprint": "source-a",
                "input_version": "phase06-llm-summary-v1",
                "ticket": {},
            }
        ],
    )
    summaries_path = _write_jsonl(
        tmp_path / "summaries.jsonl",
        [
            {
                "ticket_id": "D-1",
                "input_fingerprint": "source-a",
                "input_version": "phase06-llm-summary-v1",
                "source": "rule",
                "model": "test-model",
                "summary": _valid_summary(),
            }
        ],
    )

    validated, audit = load_validated_phase06_summaries(inputs_path, summaries_path)

    assert validated == {}
    assert audit["unmatched_or_invalid_summary_count"] == 1


def test_parse_phase06_summary_preserves_all_audited_sections() -> None:
    parsed = parse_phase06_summary(_valid_summary())

    assert parsed == {
        "root_cause_assessment": "\u7531\u4e8e\u72b6\u6001\u540c\u6b65\u5931\u8d25\uff0c\u5bfc\u81f4\u8fde\u63a5\u4e2d\u65ad\u3002",
        "fix_quality_assessment": "\u5df2\u5b8c\u6210\u4fee\u590d\u5e76\u901a\u8fc7\u56de\u5f52\u9a8c\u8bc1\u3002",
        "closure_assessment": "\u6b63\u5e38\u5173\u95ed\u3002",
        "follow_up_assessment": "\u5df2\u8bb0\u5f55\u4fee\u590d\u8ba1\u5212\u3002",
        "escape_risk_assessment": "\u65e0\u989d\u5916\u98ce\u9669\u8bc1\u636e\u3002",
    }


def test_parse_phase06_summary_rejects_duplicate_or_out_of_order_labels() -> None:
    duplicated_label = _valid_summary() + "\n" + FIX_QUALITY_LABEL + "\u91cd\u590d\u6bb5\u843d\u3002"
    out_of_order = "\n".join(
        (
            FIX_QUALITY_LABEL + "\u5df2\u7ecf\u9a8c\u8bc1\u3002",
            ROOT_CAUSE_LABEL + "\u7531\u4e8e\u540c\u6b65\u5931\u8d25\u5bfc\u81f4\u8fde\u63a5\u4e2d\u65ad\u3002",
            CLOSURE_LABEL + "\u6b63\u5e38\u5173\u95ed\u3002",
            FOLLOW_UP_LABEL + "\u6709\u8ddf\u8fdb\u3002",
            RISK_LABEL + "\u65e0\u989d\u5916\u98ce\u9669\u3002",
        )
    )

    assert parse_phase06_summary(duplicated_label) == {}
    assert parse_phase06_summary(out_of_order) == {}


def test_build_candidate_marks_llm_causal_text_for_human_review_only() -> None:
    candidate = build_rca_candidate_record(
        _detail_record(),
        _validated_summary("\u7531\u4e8e\u72b6\u6001\u540c\u6b65\u5931\u8d25\uff0c\u5bfc\u81f4\u8fde\u63a5\u4e2d\u65ad\u3002"),
    )

    assert candidate["review_priority"] == "P1"
    assert candidate["candidate_status"] == (
        "llm_causal_signal_with_recorded_remedy_evidence"
    )
    assert candidate["tree_rule_status"] == "human_review_required"
    assert candidate["approved_tree_rule"] is False
    assert candidate["llm_causal_mechanism_signal"] is True
    assert candidate["root_cause_uncertainty_signal"] is False
    assert candidate["raw_remedy_evidence_present"] is True
    assert candidate["raw_evidence_reference_present"] is True


def test_build_candidate_downgrades_uncertain_llm_root_cause() -> None:
    candidate = build_rca_candidate_record(
        _detail_record(),
        _validated_summary(
            "\u7531\u4e8e\u72b6\u6001\u540c\u6b65\u5931\u8d25\uff0c\u5bfc\u81f4\u8fde\u63a5\u4e2d\u65ad\uff0c\u4f46\u7f3a\u5c11\u539f\u59cb\u8bc1\u636e\u65e0\u6cd5\u786e\u8ba4\u3002"
        ),
    )

    assert candidate["review_priority"] == "P2"
    assert candidate["candidate_status"] == "llm_causal_signal_needs_more_evidence"
    assert candidate["tree_rule_status"] == "human_review_required"
    assert candidate["approved_tree_rule"] is False
    assert candidate["root_cause_uncertainty_signal"] is True


def test_build_candidate_downgrades_explicit_no_fix_evidence() -> None:
    candidate = build_rca_candidate_record(
        _detail_record(no_fix_evidence="\u5f53\u524d\u7248\u672c\u65e0\u4fee\u590d\u8ba1\u5212\u3002"),
        _validated_summary("\u7531\u4e8e\u72b6\u6001\u540c\u6b65\u5931\u8d25\uff0c\u5bfc\u81f4\u8fde\u63a5\u4e2d\u65ad\u3002"),
    )

    assert candidate["review_priority"] == "P2"
    assert candidate["candidate_status"] == "llm_causal_signal_needs_more_evidence"
    assert candidate["no_fix_evidence_present"] is True
    assert candidate["approved_tree_rule"] is False


def test_build_library_queues_review_candidates_without_approving_rules() -> None:
    second_record = _detail_record(repair_plan_evidence="")
    second_record["Ticket ID"] = "D-2"
    library = build_phase06_rca_candidate_library(
        [_detail_record(), second_record],
        {
            "D-1": _validated_summary(
                "\u7531\u4e8e\u72b6\u6001\u540c\u6b65\u5931\u8d25\uff0c\u5bfc\u81f4\u8fde\u63a5\u4e2d\u65ad\u3002"
            )
        },
        {"validated_summary_count": 1},
    )

    assert library["summary"] == {
        "all_ticket_count": 2,
        "validated_llm_summary_count": 1,
        "p1_candidate_count": 1,
        "p2_candidate_count": 0,
        "review_queue_count": 1,
        "approved_tree_rule_count": 0,
    }
    assert [record["ticket_id"] for record in library["review_queue"]] == ["D-1"]
    assert all(
        record["approved_tree_rule"] is False
        for record in library["all_ticket_audit"]
    )
    assert library["function_readiness"] == [
        {
            "function": "Phone Projection",
            "ticket_count": 2,
            "validated_llm_summary_count": 1,
            "llm_causal_mechanism_signal_count": 1,
            "root_cause_uncertainty_count": 0,
            "raw_remedy_evidence_count": 1,
            "p1_candidate_count": 1,
            "p2_candidate_count": 0,
            "approved_tree_rule_count": 0,
        }
    ]


def test_export_library_writes_auditable_review_sheets_json_and_html(
    tmp_path: Path,
) -> None:
    library = build_phase06_rca_candidate_library(
        [
            _detail_record(),
        ],
        {
            "D-1": _validated_summary(
                "\u7531\u4e8e\u72b6\u6001\u540c\u6b65\u5931\u8d25\uff0c\u5bfc\u81f4\u8fde\u63a5\u4e2d\u65ad\u3002"
            )
        },
        {"validated_summary_count": 1},
    )
    output_xlsx = tmp_path / "phase06-rca.xlsx"
    output_json = tmp_path / "phase06-rca.json"
    output_html = tmp_path / "phase06-rca.html"

    export_phase06_rca_candidate_library(
        library,
        output_xlsx=output_xlsx,
        output_json=output_json,
        output_html=output_html,
        source_workbook_name="Phase06full.xlsx",
    )

    workbook = load_workbook(output_xlsx, data_only=True)
    try:
        assert workbook.sheetnames == [
            "00_Overview",
            "01_RCA_Review_Queue",
            "02_All_Ticket_Audit",
            "03_Function_Readiness",
            "04_Data_Dictionary",
        ]
        queue_headers = next(
            workbook["01_RCA_Review_Queue"].iter_rows(
                min_row=1, max_row=1, values_only=True
            )
        )
        assert {
            "Review Priority",
            "LLM Root Cause Assessment",
            "Repair Plan Evidence",
            "Evidence Index",
            "Tree Rule Status",
            "Approved Tree Rule",
        }.issubset(queue_headers)
    finally:
        workbook.close()

    payload = json.loads(output_json.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "phase06-rca-candidate-library-v1"
    assert payload["policy"]["automatic_tree_rule_count"] == 0
    assert payload["review_queue"][0]["tree_rule_status"] == "human_review_required"
    html = output_html.read_text(encoding="utf-8")
    assert "No automatic decision-tree rules" in html
    assert "renderReviewQueue" in html
    assert "human_review_required" in html
    assert 'join("\\n")' in html
    assert ".metrics div:last-child { grid-column: span 2; }" not in html
    assert '<datalist id="function-options">' in html
    assert 'functionFilter.addEventListener("input"' in html
    assert "No-fix evidence" in html
    assert "record.no_fix_evidence" in html


def test_build_library_from_sources_reads_detail_workbook_and_validated_cache(
    tmp_path: Path,
) -> None:
    workbook_path = tmp_path / "Phase06full.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "05_\u5168\u90e8\u660e\u7ec6"
    detail_record = _detail_record()
    sheet.append(list(detail_record))
    sheet.append(list(detail_record.values()))
    workbook.save(workbook_path)
    workbook.close()
    inputs_path = _write_jsonl(
        tmp_path / "inputs.jsonl",
        [
            {
                "ticket_id": "D-1",
                "input_fingerprint": "source-a",
                "input_version": "phase06-llm-summary-v1",
                "ticket": {},
            }
        ],
    )
    summaries_path = _write_jsonl(
        tmp_path / "summaries.jsonl",
        [
            {
                "ticket_id": "D-1",
                "input_fingerprint": "source-a",
                "input_version": "phase06-llm-summary-v1",
                "source": "llm",
                "model": "test-model",
                "summary": _valid_summary(),
            }
        ],
    )

    library = build_phase06_rca_candidate_library_from_sources(
        workbook_path,
        inputs_path,
        summaries_path,
    )

    assert library["summary"]["all_ticket_count"] == 1
    assert library["summary"]["validated_llm_summary_count"] == 1
    assert library["review_queue"][0]["ticket_id"] == "D-1"