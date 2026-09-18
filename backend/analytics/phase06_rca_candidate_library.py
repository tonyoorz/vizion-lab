"""Build an auditable Phase 06 RCA and remedy candidate library.

LLM summary text remains a candidate assessment. It is never promoted to an
approved diagnostic or repair rule by this module.
"""
from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


PHASE06_INPUT_VERSION = "phase06-llm-summary-v1"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORKBOOK = (
    REPO_ROOT
    / "exports"
    / "idcevo_phase06"
    / "IDCEVO_Phase06_Resolution_Quality_20260917.xlsx"
)
DEFAULT_INPUTS = REPO_ROOT / "exports" / "idcevo_phase06" / "llm" / "phase06_inputs_v1.jsonl"
DEFAULT_SUMMARIES = (
    REPO_ROOT / "exports" / "idcevo_phase06" / "llm" / "phase06_summaries_v1_pro.jsonl"
)
DEFAULT_OUTPUT_XLSX = (
    REPO_ROOT / "exports" / "idcevo_phase06" / "IDCEVO_Phase06_RCA_Candidate_Library_V1.xlsx"
)
DEFAULT_OUTPUT_JSON = (
    REPO_ROOT / "exports" / "idcevo_phase06" / "IDCEVO_Phase06_RCA_Candidate_Library_V1.json"
)
DEFAULT_OUTPUT_HTML = (
    REPO_ROOT / "exports" / "idcevo_phase06" / "IDCEVO_Phase06_RCA_Candidate_Library_V1.html"
)
DETAIL_SHEET = "05_\u5168\u90e8\u660e\u7ec6"
PHASE06_SUMMARY_SECTIONS = (
    ("root_cause_assessment", "\u6839\u56e0\uff1a"),
    ("fix_quality_assessment", "\u4fee\u590d\u8d28\u91cf\uff1a"),
    ("closure_assessment", "Reject/\u5173\u95ed\u539f\u56e0\uff1a"),
    ("follow_up_assessment", "\u540e\u7eed\u95ed\u73af\uff1a"),
    ("escape_risk_assessment", "\u98ce\u9669\u5224\u65ad\u4e0e\u9003\u9038\u56de\u987e\uff1a"),
)

DETAIL_FIELD_NAMES = {
    "ticket_id": "Ticket ID",
    "function": "\u529f\u80fd",
    "solution_cluster": "Solution Cluster",
    "defect_category": "Defect Category",
    "assigned_ecu": "Assigned ECU",
    "software_version": "Software Version",
    "closure_disposition": "\u5173\u95ed\u5904\u7f6e",
    "ticket_title": "Ticket \u6807\u9898",
    "problem_description_excerpt": "\u95ee\u9898\u63cf\u8ff0\u6458\u5f55",
    "evidence_index": "\u8bc1\u636e\u539f\u6587\u7d22\u5f15",
    "repair_plan_evidence": "\u4fee\u590d\u8ba1\u5212\u8bc1\u636e",
    "decision_evidence": "\u51b3\u7b56\u8bc1\u636e",
    "no_fix_evidence": "\u65e0\u4fee\u590d\u8bc1\u636e",
}
EMPTY_EVIDENCE_VALUES = {
    "",
    "(blank)",
    "none",
    "n/a",
    "na",
    "not recorded",
    "unknown",
    "not applicable",
    "\u65e0",
    "\u672a\u8bb0\u5f55",
    "\u6682\u65e0",
}
CAUSE_MECHANISM_PATTERN = re.compile(
    r"\u7531\u4e8e|\u5bfc\u81f4|\u5f15\u53d1|\u9020\u6210|\u81f4\u4f7f|"
    r"\u6839\u672c\u539f\u56e0(?:\u4e3a|\u662f)|\u539f\u56e0(?:\u4e3a|\u662f)"
)
ROOT_CAUSE_UNCERTAINTY_PATTERN = re.compile(
    r"\u672a\u8bb0\u5f55|\u672a\u63d0\u4f9b|\u65e0\u6cd5|\u4e0d\u8db3|"
    r"\u5f85\u6838\u5b9e|\u4e0d\u660e\u786e|\u7f3a\u5c11|\u672a\u80fd|"
    r"\u4e0d\u80fd|\u5c1a\u672a|\u4e0d\u5145\u5206|\u53ef\u80fd|"
    r"\u7591\u4f3c|\u4ec5\u505c\u7559|\u4ec5\u6307\u5411"
)
FIX_POSITIVE_VERIFICATION_PATTERN = re.compile(
    r"(?:\u5df2|\u5df2\u7ecf|\u901a\u8fc7|\u5b8c\u6210|\u7ecf|\u8fdb\u884c\u4e86).{0,12}"
    r"(?:\u9a8c\u8bc1|\u6d4b\u8bd5|\u56de\u5f52|\u786e\u8ba4)|"
    r"(?:\u9a8c\u8bc1|\u6d4b\u8bd5|\u56de\u5f52|\u786e\u8ba4).{0,12}"
    r"(?:\u901a\u8fc7|\u5b8c\u6210|\u6709\u6548|\u6210\u529f)"
)
FIX_VERIFICATION_GAP_PATTERN = re.compile(
    r"(?:\u672a|\u5c1a\u672a|\u7f3a\u4e4f|\u7f3a\u5c11|\u6ca1\u6709|\u65e0|"
    r"\u65e0\u6cd5|\u4e0d\u8db3|\u5f85|\u4e0d\u5145\u5206|\u5b58\u7591).{0,24}"
    r"(?:\u9a8c\u8bc1|\u6d4b\u8bd5|\u56de\u5f52|\u786e\u8ba4)|"
    r"(?:\u9a8c\u8bc1|\u6d4b\u8bd5|\u56de\u5f52|\u786e\u8ba4).{0,24}"
    r"(?:\u4e0d\u8db3|\u7f3a\u4e4f|\u7f3a\u5c11|\u672a|\u65e0\u6cd5|\u4e0d\u5145\u5206|\u5b58\u7591)"
)
AUDIT_COLUMNS = (
    ("Review Priority", "review_priority"),
    ("Candidate Status", "candidate_status"),
    ("Tree Rule Status", "tree_rule_status"),
    ("Approved Tree Rule", "approved_tree_rule"),
    ("Ticket ID", "ticket_id"),
    ("Function", "function"),
    ("Solution Cluster", "solution_cluster"),
    ("Defect Category", "defect_category"),
    ("Closure Disposition", "closure_disposition"),
    ("Ticket Title", "ticket_title"),
    ("Problem Description Excerpt", "problem_description_excerpt"),
    ("LLM Root Cause Assessment", "llm_root_cause_assessment"),
    ("LLM Fix Quality Assessment", "llm_fix_quality_assessment"),
    ("LLM Closure Assessment", "llm_closure_assessment"),
    ("LLM Follow-up Assessment", "llm_follow_up_assessment"),
    ("LLM Escape Risk Assessment", "llm_escape_risk_assessment"),
    ("LLM Source", "llm_summary_source"),
    ("LLM Model", "llm_model"),
    ("Assigned ECU", "assigned_ecu"),
    ("Software Version", "software_version"),
    ("Repair Plan Evidence", "repair_plan_evidence"),
    ("Decision Evidence", "decision_evidence"),
    ("No Fix Evidence", "no_fix_evidence"),
    ("Evidence Index", "evidence_index"),
    ("LLM Causal Mechanism Signal", "llm_causal_mechanism_signal"),
    ("Root Cause Uncertainty Signal", "root_cause_uncertainty_signal"),
    ("Positive Fix Verification Signal", "fix_positive_verification_signal"),
    ("Fix Verification Gap Signal", "fix_verification_gap_signal"),
    ("Repair Plan Evidence Present", "repair_plan_evidence_present"),
    ("Decision Evidence Present", "decision_evidence_present"),
    ("No Fix Evidence Present", "no_fix_evidence_present"),
    ("Raw Remedy Evidence Present", "raw_remedy_evidence_present"),
    ("Raw Evidence Reference Present", "raw_evidence_reference_present"),
    ("Review Rationale", "review_rationale"),
)
FUNCTION_READINESS_COLUMNS = (
    ("Function", "function"),
    ("Ticket Count", "ticket_count"),
    ("Validated LLM Summary Count", "validated_llm_summary_count"),
    ("LLM Causal Mechanism Signal Count", "llm_causal_mechanism_signal_count"),
    ("Root Cause Uncertainty Count", "root_cause_uncertainty_count"),
    ("Raw Remedy Evidence Count", "raw_remedy_evidence_count"),
    ("P1 Candidate Count", "p1_candidate_count"),
    ("P2 Candidate Count", "p2_candidate_count"),
    ("Approved Tree Rule Count", "approved_tree_rule_count"),
)
HEADER_FILL = PatternFill("solid", fgColor="164A5C")
HEADER_FONT = Font(color="FFFFFF", bold=True)
HEADER_ALIGNMENT = Alignment(horizontal="center", vertical="center", wrap_text=True)
BODY_ALIGNMENT = Alignment(vertical="top", wrap_text=True)


def _text(value: object) -> str:
    return str(value or "").strip()


def _has_recorded_evidence(value: object) -> bool:
    return _text(value).casefold() not in EMPTY_EVIDENCE_VALUES


def _detail_value(detail_record: Mapping[str, Any], field_name: str) -> str:
    return _text(detail_record.get(DETAIL_FIELD_NAMES[field_name]))


def _read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    resolved_path = Path(path)
    with resolved_path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"Invalid JSONL record at {resolved_path}:{line_number}"
                ) from error
            if not isinstance(record, dict):
                raise ValueError(
                    f"JSONL record must be an object at {resolved_path}:{line_number}"
                )
            records.append(record)
    return records


def parse_phase06_summary(summary: str) -> dict[str, str]:
    """Extract the fixed sections emitted by the Phase 06 LLM summary runner."""
    positions = []
    for field_name, label in PHASE06_SUMMARY_SECTIONS:
        matches = list(re.finditer(rf"(?m)^{re.escape(label)}", summary))
        if len(matches) != 1:
            return {}
        positions.append((matches[0].start(), field_name, label))
    if [position for position, _, _ in positions] != sorted(
        position for position, _, _ in positions
    ):
        return {}
    parsed: dict[str, str] = {}
    for index, (start, field_name, label) in enumerate(positions):
        end = positions[index + 1][0] if index + 1 < len(positions) else len(summary)
        parsed[field_name] = summary[start + len(label):end].strip()
    return parsed


def _input_index(
    records: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, str]], set[str], int]:
    index: dict[str, dict[str, str]] = {}
    ambiguous_ticket_ids: set[str] = set()
    duplicate_count = 0
    for record in records:
        ticket_id = _text(record.get("ticket_id"))
        if not ticket_id:
            continue
        if ticket_id in index:
            duplicate_count += 1
            ambiguous_ticket_ids.add(ticket_id)
            del index[ticket_id]
            continue
        if ticket_id in ambiguous_ticket_ids:
            duplicate_count += 1
            continue
        index[ticket_id] = {
            "input_fingerprint": _text(record.get("input_fingerprint")),
            "input_version": _text(record.get("input_version")),
        }
    return index, ambiguous_ticket_ids, duplicate_count


def load_validated_phase06_summaries(
    inputs_path: str | Path,
    summaries_path: str | Path,
) -> tuple[dict[str, dict[str, str]], dict[str, int]]:
    """Load only summaries that exactly match the cached Phase 06 input record."""
    input_records = _read_jsonl(inputs_path)
    summary_records = _read_jsonl(summaries_path)
    (
        inputs_by_ticket_id,
        ambiguous_input_ticket_ids,
        input_duplicate_count,
    ) = _input_index(input_records)
    validated: dict[str, dict[str, str]] = {}
    audit = {
        "input_record_count": len(input_records),
        "unique_input_ticket_count": len(inputs_by_ticket_id)
        + len(ambiguous_input_ticket_ids),
        "input_duplicate_count": input_duplicate_count,
        "ambiguous_input_ticket_count": len(ambiguous_input_ticket_ids),
        "summary_record_count": len(summary_records),
        "validated_summary_count": 0,
        "unmatched_or_invalid_summary_count": 0,
        "ambiguous_input_summary_count": 0,
        "missing_required_section_count": 0,
        "malformed_summary_structure_count": 0,
        "duplicate_valid_summary_count": 0,
    }

    for record in summary_records:
        ticket_id = _text(record.get("ticket_id"))
        if ticket_id in ambiguous_input_ticket_ids:
            audit["ambiguous_input_summary_count"] += 1
            audit["unmatched_or_invalid_summary_count"] += 1
            continue
        input_record = inputs_by_ticket_id.get(ticket_id)
        summary = _text(record.get("summary"))
        if (
            _text(record.get("source")).casefold() != "llm"
            or input_record is None
            or not summary
            or _text(record.get("input_fingerprint"))
            != input_record["input_fingerprint"]
            or _text(record.get("input_version")) != input_record["input_version"]
        ):
            audit["unmatched_or_invalid_summary_count"] += 1
            continue

        parsed = parse_phase06_summary(summary)
        if not parsed:
            audit["malformed_summary_structure_count"] += 1
            audit["missing_required_section_count"] += 1
            continue
        if any(field_name not in parsed or not parsed[field_name] for field_name, _ in PHASE06_SUMMARY_SECTIONS):
            audit["missing_required_section_count"] += 1
            continue
        if ticket_id in validated:
            audit["duplicate_valid_summary_count"] += 1
            continue

        validated[ticket_id] = {
            "ticket_id": ticket_id,
            "input_fingerprint": input_record["input_fingerprint"],
            "input_version": input_record["input_version"],
            "model": _text(record.get("model")),
            "source": "llm",
            "summary": summary,
            **parsed,
        }

    audit["validated_summary_count"] = len(validated)
    return validated, audit


def build_rca_candidate_record(
    detail_record: Mapping[str, Any],
    summary_record: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Build an auditable human-review record without approving a tree rule."""
    detail_values = {
        field_name: _detail_value(detail_record, field_name)
        for field_name in DETAIL_FIELD_NAMES
    }
    root_cause_assessment = _text(
        summary_record.get("root_cause_assessment") if summary_record else ""
    )
    fix_quality_assessment = _text(
        summary_record.get("fix_quality_assessment") if summary_record else ""
    )
    repair_plan_present = _has_recorded_evidence(
        detail_values["repair_plan_evidence"]
    )
    decision_present = _has_recorded_evidence(detail_values["decision_evidence"])
    no_fix_present = _has_recorded_evidence(detail_values["no_fix_evidence"])
    raw_remedy_present = repair_plan_present or decision_present
    evidence_reference_present = _has_recorded_evidence(detail_values["evidence_index"])
    causal_mechanism_signal = bool(CAUSE_MECHANISM_PATTERN.search(root_cause_assessment))
    root_cause_uncertainty_signal = bool(
        ROOT_CAUSE_UNCERTAINTY_PATTERN.search(root_cause_assessment)
    )
    fix_positive_verification_signal = bool(
        FIX_POSITIVE_VERIFICATION_PATTERN.search(fix_quality_assessment)
    )
    fix_verification_gap_signal = bool(
        FIX_VERIFICATION_GAP_PATTERN.search(fix_quality_assessment)
    )

    if summary_record is None:
        review_priority = "Not shortlisted"
        candidate_status = "missing_validated_llm_summary"
        review_rationale = "No fingerprint-validated Phase 06 LLM assessment is available."
    elif (
        causal_mechanism_signal
        and raw_remedy_present
        and evidence_reference_present
        and not root_cause_uncertainty_signal
        and not no_fix_present
    ):
        review_priority = "P1"
        candidate_status = "llm_causal_signal_with_recorded_remedy_evidence"
        review_rationale = (
            "LLM causal signal, recorded remedy evidence, and raw evidence reference "
            "are available; human review is still required."
        )
    elif causal_mechanism_signal:
        review_priority = "P2"
        candidate_status = "llm_causal_signal_needs_more_evidence"
        review_rationale = (
            "LLM causal signal is present, but causal certainty, remedy evidence, "
            "or raw evidence review is incomplete."
        )
    elif raw_remedy_present:
        review_priority = "P2"
        candidate_status = "recorded_remedy_evidence_needs_causal_evidence"
        review_rationale = (
            "Recorded remedy evidence is present, but no LLM causal-mechanism signal "
            "was found."
        )
    else:
        review_priority = "Not shortlisted"
        candidate_status = "not_shortlisted"
        review_rationale = (
            "No causal-mechanism signal or recorded remedy/decision evidence was found."
        )

    return {
        **detail_values,
        "llm_summary_source": _text(summary_record.get("source")) if summary_record else "",
        "llm_model": _text(summary_record.get("model")) if summary_record else "",
        "llm_root_cause_assessment": root_cause_assessment,
        "llm_fix_quality_assessment": fix_quality_assessment,
        "llm_closure_assessment": _text(
            summary_record.get("closure_assessment") if summary_record else ""
        ),
        "llm_follow_up_assessment": _text(
            summary_record.get("follow_up_assessment") if summary_record else ""
        ),
        "llm_escape_risk_assessment": _text(
            summary_record.get("escape_risk_assessment") if summary_record else ""
        ),
        "llm_causal_mechanism_signal": causal_mechanism_signal,
        "root_cause_uncertainty_signal": root_cause_uncertainty_signal,
        "fix_positive_verification_signal": fix_positive_verification_signal,
        "fix_verification_gap_signal": fix_verification_gap_signal,
        "repair_plan_evidence_present": repair_plan_present,
        "decision_evidence_present": decision_present,
        "no_fix_evidence_present": no_fix_present,
        "raw_remedy_evidence_present": raw_remedy_present,
        "raw_evidence_reference_present": evidence_reference_present,
        "review_priority": review_priority,
        "candidate_status": candidate_status,
        "review_rationale": review_rationale,
        "tree_rule_status": "human_review_required",
        "approved_tree_rule": False,
    }


def build_phase06_rca_candidate_library(
    detail_records: list[Mapping[str, Any]],
    validated_summaries: Mapping[str, Mapping[str, Any]],
    validation_audit: Mapping[str, int],
) -> dict[str, Any]:
    """Build review records and readiness counts without creating tree rules."""
    all_ticket_audit = [
        build_rca_candidate_record(
            detail_record,
            validated_summaries.get(_detail_value(detail_record, "ticket_id")),
        )
        for detail_record in detail_records
    ]
    review_queue = sorted(
        (
            candidate
            for candidate in all_ticket_audit
            if candidate["review_priority"] in {"P1", "P2"}
        ),
        key=lambda candidate: (
            {"P1": 0, "P2": 1}.get(candidate["review_priority"], 2),
            candidate["function"].casefold(),
            candidate["ticket_id"],
        ),
    )
    readiness_by_function: dict[str, dict[str, Any]] = {}
    for candidate in all_ticket_audit:
        function_name = candidate["function"] or "(Not recorded)"
        readiness = readiness_by_function.setdefault(
            function_name,
            {
                "function": function_name,
                "ticket_count": 0,
                "validated_llm_summary_count": 0,
                "llm_causal_mechanism_signal_count": 0,
                "root_cause_uncertainty_count": 0,
                "raw_remedy_evidence_count": 0,
                "p1_candidate_count": 0,
                "p2_candidate_count": 0,
                "approved_tree_rule_count": 0,
            },
        )
        readiness["ticket_count"] += 1
        readiness["validated_llm_summary_count"] += bool(
            candidate["llm_summary_source"]
        )
        readiness["llm_causal_mechanism_signal_count"] += bool(
            candidate["llm_causal_mechanism_signal"]
        )
        readiness["root_cause_uncertainty_count"] += bool(
            candidate["root_cause_uncertainty_signal"]
        )
        readiness["raw_remedy_evidence_count"] += bool(
            candidate["raw_remedy_evidence_present"]
        )
        readiness["p1_candidate_count"] += candidate["review_priority"] == "P1"
        readiness["p2_candidate_count"] += candidate["review_priority"] == "P2"

    function_readiness = sorted(
        readiness_by_function.values(),
        key=lambda readiness: readiness["function"].casefold(),
    )
    summary = {
        "all_ticket_count": len(all_ticket_audit),
        "validated_llm_summary_count": len(validated_summaries),
        "p1_candidate_count": sum(
            candidate["review_priority"] == "P1" for candidate in all_ticket_audit
        ),
        "p2_candidate_count": sum(
            candidate["review_priority"] == "P2" for candidate in all_ticket_audit
        ),
        "review_queue_count": len(review_queue),
        "approved_tree_rule_count": 0,
    }
    return {
        "summary": summary,
        "validation_audit": dict(validation_audit),
        "all_ticket_audit": all_ticket_audit,
        "review_queue": review_queue,
        "function_readiness": function_readiness,
    }


def load_phase06_detail_records(workbook_path: str | Path) -> list[dict[str, Any]]:
    """Read the required Phase 06 ticket fields from the detail sheet."""
    resolved_path = Path(workbook_path)
    workbook = load_workbook(resolved_path, read_only=True, data_only=True)
    try:
        if DETAIL_SHEET not in workbook.sheetnames:
            raise ValueError(
                f"Phase 06 workbook has no required sheet: {DETAIL_SHEET}"
            )
        sheet = workbook[DETAIL_SHEET]
        headers = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), ())
        header_index = {
            _text(header): index for index, header in enumerate(headers) if _text(header)
        }
        required_headers = tuple(DETAIL_FIELD_NAMES.values())
        missing_headers = [
            header for header in required_headers if header not in header_index
        ]
        if missing_headers:
            raise ValueError(
                "Phase 06 detail sheet is missing required headers: "
                + ", ".join(missing_headers)
            )
        records: list[dict[str, Any]] = []
        for row in sheet.iter_rows(min_row=2, values_only=True):
            if not any(_has_recorded_evidence(value) for value in row):
                continue
            records.append(
                {
                    header: row[header_index[header]]
                    if header_index[header] < len(row)
                    else ""
                    for header in required_headers
                }
            )
        return records
    finally:
        workbook.close()


def build_phase06_rca_candidate_library_from_sources(
    workbook_path: str | Path,
    inputs_path: str | Path,
    summaries_path: str | Path,
) -> dict[str, Any]:
    """Build a review-only library from a Phase 06 workbook and validated cache."""
    detail_records = load_phase06_detail_records(workbook_path)
    validated_summaries, validation_audit = load_validated_phase06_summaries(
        inputs_path, summaries_path
    )
    library = build_phase06_rca_candidate_library(
        detail_records, validated_summaries, validation_audit
    )
    library["source_audit"] = {
        "source_workbook_name": Path(workbook_path).name,
        "detail_sheet": DETAIL_SHEET,
        "detail_record_count": len(detail_records),
        "unmatched_detail_ticket_count": sum(
            _detail_value(record, "ticket_id") not in validated_summaries
            for record in detail_records
        ),
    }
    return library


def _excel_value(value: object) -> object:
    if isinstance(value, str) and len(value) > 32_767:
        suffix = "\n[Truncated at Excel cell limit.]"
        return value[: 32_767 - len(suffix)] + suffix
    return value


def _style_table(sheet, column_count: int, widths: list[int]) -> None:
    for cell in sheet[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = HEADER_ALIGNMENT
    for row in sheet.iter_rows(min_row=2, max_col=column_count):
        for cell in row:
            cell.alignment = BODY_ALIGNMENT
    sheet.freeze_panes = "A2"
    if sheet.max_row > 1:
        sheet.auto_filter.ref = f"A1:{get_column_letter(column_count)}{sheet.max_row}"
    for column_index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(column_index)].width = width


def _write_record_sheet(
    workbook: Workbook,
    sheet_name: str,
    records: list[Mapping[str, Any]],
) -> None:
    sheet = workbook.create_sheet(sheet_name)
    sheet.append([header for header, _ in AUDIT_COLUMNS])
    for record in records:
        sheet.append([_excel_value(record.get(key, "")) for _, key in AUDIT_COLUMNS])
    _style_table(
        sheet,
        len(AUDIT_COLUMNS),
        [14, 42, 24, 18, 16, 28, 26, 26, 34, 52, 68, 82, 82, 64, 64, 64, 16, 24, 22, 24, 64, 64, 64, 48, 16, 18, 18, 18, 18, 18, 18, 18, 18, 72],
    )


def _write_overview_sheet(
    workbook: Workbook,
    library: Mapping[str, Any],
    source_workbook_name: str,
) -> None:
    sheet = workbook.active
    sheet.title = "00_Overview"
    sheet.append(["Phase 06 RCA Candidate Library"])
    sheet.append([])
    sheet.append(["Source workbook", source_workbook_name])
    sheet.append(["Decision-tree policy", "No automatic decision-tree rules are generated."])
    sheet.append([
        "LLM policy",
        "Fingerprint-validated LLM assessments are review candidates, not verified RCA facts.",
    ])
    sheet.append([])
    sheet.append(["Metric", "Count", "Meaning"])
    summary = library["summary"]
    validation_audit = library["validation_audit"]
    overview_rows = (
        (
            "All Ticket audit records",
            summary["all_ticket_count"],
            "Ticket rows read from the Phase 06 detail workbook.",
        ),
        (
            "Validated LLM summaries",
            summary["validated_llm_summary_count"],
            "LLM source, Ticket ID, fingerprint, and input version all matched.",
        ),
        (
            "P1 review candidates",
            summary["p1_candidate_count"],
            "LLM causal signal plus raw remedy and evidence-reference signals; human review required.",
        ),
        (
            "P2 review candidates",
            summary["p2_candidate_count"],
            "Causal or remedy signal exists, but review evidence is incomplete or uncertain.",
        ),
        (
            "Review queue",
            summary["review_queue_count"],
            "P1 and P2 candidates only.",
        ),
        (
            "Approved tree rules",
            summary["approved_tree_rule_count"],
            "Always zero. Promotion needs explicit human approval and source evidence review.",
        ),
        (
            "Invalid or unmatched summaries",
            validation_audit.get("unmatched_or_invalid_summary_count", 0),
            "Never used as LLM candidate evidence.",
        ),
        (
            "Missing required LLM sections",
            validation_audit.get("missing_required_section_count", 0),
            "Never used as LLM candidate evidence.",
        ),
        (
            "Ambiguous cache inputs",
            validation_audit.get("ambiguous_input_ticket_count", 0),
            "Duplicate Ticket IDs in the input cache are excluded from LLM candidate use.",
        ),
    )
    for row in overview_rows:
        sheet.append(list(row))
    for cell in sheet[1]:
        cell.font = Font(bold=True, size=14, color="164A5C")
    for cell in sheet[7]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = HEADER_ALIGNMENT
    for row in sheet.iter_rows(min_row=8, max_col=3):
        for cell in row:
            cell.alignment = BODY_ALIGNMENT
    sheet.column_dimensions["A"].width = 34
    sheet.column_dimensions["B"].width = 18
    sheet.column_dimensions["C"].width = 90


def _write_function_readiness_sheet(
    workbook: Workbook,
    function_readiness: list[Mapping[str, Any]],
) -> None:
    sheet = workbook.create_sheet("03_Function_Readiness")
    sheet.append([header for header, _ in FUNCTION_READINESS_COLUMNS])
    for readiness in function_readiness:
        sheet.append(
            [_excel_value(readiness.get(key, "")) for _, key in FUNCTION_READINESS_COLUMNS]
        )
    _style_table(
        sheet,
        len(FUNCTION_READINESS_COLUMNS),
        [36, 16, 26, 34, 28, 28, 20, 20, 26],
    )


def _write_data_dictionary_sheet(workbook: Workbook) -> None:
    sheet = workbook.create_sheet("04_Data_Dictionary")
    sheet.append(["Field", "Layer", "Definition", "Policy"])
    rows = (
        (
            "LLM Root Cause Assessment",
            "LLM candidate",
            "The root-cause assessment segment from a fingerprint-validated Phase 06 LLM summary.",
            "It is not a verified technical RCA fact.",
        ),
        (
            "LLM Causal Mechanism Signal",
            "Heuristic",
            "A conservative lexical signal in the LLM root-cause assessment.",
            "It only prioritizes human review and never creates a tree rule.",
        ),
        (
            "Root Cause Uncertainty Signal",
            "Heuristic",
            "Evidence-gap or uncertainty wording in the LLM root-cause assessment.",
            "A signal of incomplete evidence, not a root-cause conclusion.",
        ),
        (
            "Repair Plan Evidence / Decision Evidence",
            "Ticket evidence",
            "Recorded source excerpts retained by the Phase 06 report.",
            "Review the corresponding raw evidence before asserting a remedy or decision.",
        ),
        (
            "No Fix Evidence",
            "Ticket evidence",
            "Recorded source excerpt that states a repair is absent, deferred, or otherwise unavailable.",
            "Blocks P1 priority and must remain visible during human review.",
        ),
        (
            "Evidence Index",
            "Traceability",
            "Reference to the Ticket description, comment, and history evidence in the source workbook.",
            "Required for an auditable review candidate.",
        ),
        (
            "Review Priority",
            "Review workflow",
            "P1 is a higher-evidence LLM and Ticket candidate; P2 needs more evidence; unshortlisted rows are retained in audit.",
            "Priority is not confidence, approval, or a vehicle diagnostic conclusion.",
        ),
        (
            "Tree Rule Status",
            "Governance",
            "Every generated row is human_review_required.",
            "No automatic decision-tree rules are generated by this library.",
        ),
        (
            "Approved Tree Rule",
            "Governance",
            "Always False in generated output.",
            "Only an external documented human-review process may promote a candidate.",
        ),
    )
    for row in rows:
        sheet.append(list(row))
    _style_table(sheet, 4, [36, 22, 78, 84])


def _serialized_html_data(value: object) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def render_phase06_rca_candidate_review_html(
    library: Mapping[str, Any],
    source_workbook_name: str,
) -> str:
    """Render a compact candidate-review page, not a diagnostic decision tree."""
    review_queue = library["review_queue"]
    summary = library["summary"]
    review_queue_data = _serialized_html_data(review_queue)
    summary_data = _serialized_html_data(summary)
    source_workbook_data = _serialized_html_data(source_workbook_name)
    template = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Phase 06 RCA Candidate Review</title>
<style>
:root { color: #18252b; background: #eef3f2; font-family: Aptos, Candara, "Segoe UI", sans-serif; }
* { box-sizing: border-box; }
body { margin: 0; }
main { max-width: 1560px; margin: 0 auto; padding: 30px 28px 52px; }
header { padding: 0 0 18px; border-bottom: 4px solid #167a72; }
h1 { margin: 0; color: #123f4e; font-size: 30px; letter-spacing: 0; }
p { line-height: 1.5; }
.subtitle { max-width: 920px; margin: 8px 0 0; color: #506069; }
.notice { margin: 18px 0; padding: 12px 14px; border-left: 4px solid #b74f37; background: #fff4ef; color: #603326; font-weight: 700; }
.metrics { display: grid; grid-template-columns: repeat(6, minmax(118px, 1fr)); gap: 1px; background: #c8d4d3; border: 1px solid #c8d4d3; }
.metrics div { min-height: 76px; padding: 12px 14px; background: #fff; }
.metrics span { display: block; color: #627078; font-size: 12px; }
.metrics strong { display: block; margin-top: 7px; color: #123f4e; font-size: 19px; }
.path { display: grid; grid-template-columns: repeat(5, minmax(120px, 1fr)); gap: 14px; margin: 22px 0; align-items: center; }
.path div { min-height: 76px; padding: 12px; border: 1px solid #95aaa7; border-radius: 5px; background: #fff; color: #254d50; font-size: 13px; font-weight: 700; line-height: 1.35; }
.path div:not(:last-child)::after { content: "->"; float: right; margin-right: -24px; color: #167a72; font-size: 19px; }
.controls { display: flex; gap: 14px; flex-wrap: wrap; align-items: end; margin: 22px 0 12px; }
label { display: grid; gap: 6px; color: #34444b; font-size: 13px; font-weight: 700; }
select, input { min-width: 220px; min-height: 40px; padding: 8px 10px; border: 1px solid #829590; border-radius: 4px; background: #fff; color: #18252b; font: inherit; }
input { min-width: min(400px, 100%); }
.results { margin: 0 0 10px; color: #52616a; font-size: 13px; }
.table-shell { overflow: auto; border: 1px solid #bdccca; background: #fff; }
table { min-width: 1440px; width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 9px 10px; border: 1px solid #d2dcda; text-align: left; vertical-align: top; line-height: 1.4; }
th { position: sticky; top: 0; background: #123f4e; color: #fff; z-index: 1; }
td.summary { min-width: 320px; max-width: 430px; }
td.evidence { min-width: 260px; max-width: 360px; }
.priority { font-weight: 700; color: #9a4d18; }
.status { color: #53656c; font-family: Consolas, monospace; font-size: 12px; }
.pagination { display: flex; gap: 10px; align-items: center; margin: 14px 0; }
button { min-width: 38px; min-height: 36px; padding: 7px 12px; border: 1px solid #6a827f; border-radius: 4px; background: #fff; color: #174d51; font: inherit; font-weight: 700; cursor: pointer; }
button:disabled { cursor: default; opacity: 0.45; }
@media (max-width: 760px) { main { padding: 22px 16px 36px; } h1 { font-size: 24px; } .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); } .path { grid-template-columns: 1fr; gap: 8px; } .path div:not(:last-child)::after { content: "v"; float: none; display: block; margin: 6px 0 -16px; text-align: center; } select, input { width: 100%; min-width: 0; } .controls { display: grid; grid-template-columns: 1fr; } }
</style>
</head>
<body>
<main>
  <header>
    <h1>Phase 06 RCA Candidate Review</h1>
    <p class="subtitle">Source workbook: <span id="source-workbook"></span>. This is a review queue for evidence-cited candidate patterns, not an after-sales diagnostic tree.</p>
  </header>
  <p class="notice">No automatic decision-tree rules are generated. Every candidate remains human_review_required.</p>
  <section class="metrics" aria-label="Review metrics">
    <div><span>All ticket audit rows</span><strong id="all-count"></strong></div>
    <div><span>Validated LLM summaries</span><strong id="validated-count"></strong></div>
    <div><span>P1 review candidates</span><strong id="p1-count"></strong></div>
    <div><span>P2 review candidates</span><strong id="p2-count"></strong></div>
    <div><span>Review queue</span><strong id="queue-count"></strong></div>
    <div><span>Approved tree rules</span><strong id="approved-count"></strong></div>
  </section>
  <section class="path" aria-label="Candidate promotion path"><div>LLM causal signal</div><div>Raw Ticket evidence</div><div>Recorded remedy evidence</div><div>Human RCA review</div><div>Externally approved tree branch</div></section>
  <section class="controls">
        <label for="function-filter">Function<input id="function-filter" list="function-options" type="search" placeholder="All functions"></label>
        <datalist id="function-options"></datalist>
    <label for="priority-filter">Priority<select id="priority-filter"><option value="all">All priorities</option><option value="P1">P1</option><option value="P2">P2</option></select></label>
    <label for="search-filter">Search<input id="search-filter" type="search" placeholder="Function, title, root cause, or remedy"></label>
  </section>
  <p class="results" id="results"></p>
    <section class="table-shell"><table><thead><tr><th>Priority</th><th>Function</th><th>Ticket</th><th>Candidate status</th><th>LLM root-cause assessment</th><th>LLM fix-quality assessment</th><th>Recorded remedy / decision evidence</th><th>No-fix evidence</th><th>Evidence index</th><th>Tree rule status</th></tr></thead><tbody id="review-rows"></tbody></table></section>
  <section class="pagination"><button id="previous-page" type="button" aria-label="Previous page">Previous</button><span id="page-status"></span><button id="next-page" type="button" aria-label="Next page">Next</button></section>
</main>
<script>
const reviewQueue = __REVIEW_QUEUE__;
const summary = __SUMMARY__;
const sourceWorkbook = __SOURCE_WORKBOOK__;
const state = { page: 1, pageSize: 50 };
const functionFilter = document.getElementById("function-filter");
const functionOptions = document.getElementById("function-options");
const priorityFilter = document.getElementById("priority-filter");
const searchFilter = document.getElementById("search-filter");
document.getElementById("source-workbook").textContent = sourceWorkbook;
document.getElementById("all-count").textContent = String(summary.all_ticket_count || 0);
document.getElementById("validated-count").textContent = String(summary.validated_llm_summary_count || 0);
document.getElementById("p1-count").textContent = String(summary.p1_candidate_count || 0);
document.getElementById("p2-count").textContent = String(summary.p2_candidate_count || 0);
document.getElementById("queue-count").textContent = String(summary.review_queue_count || 0);
document.getElementById("approved-count").textContent = String(summary.approved_tree_rule_count || 0);
[...new Set(reviewQueue.map((record) => record.function || "(Not recorded)"))].sort((left, right) => left.localeCompare(right)).forEach((name) => functionOptions.append(new Option(name, name)));
function filteredRecords() { const functionQuery = functionFilter.value.trim().toLocaleLowerCase(); const priority = priorityFilter.value; const query = searchFilter.value.trim().toLocaleLowerCase(); return reviewQueue.filter((record) => { const sourceFunction = (record.function || "(Not recorded)").toLocaleLowerCase(); const matchesFunction = !functionQuery || sourceFunction.includes(functionQuery); const matchesPriority = priority === "all" || record.review_priority === priority; const searchable = [record.function, record.ticket_title, record.llm_root_cause_assessment, record.llm_fix_quality_assessment, record.repair_plan_evidence, record.decision_evidence, record.no_fix_evidence].join(" ").toLocaleLowerCase(); return matchesFunction && matchesPriority && (!query || searchable.includes(query)); }); }
function appendCell(row, value, className = "") { const cell = document.createElement("td"); if (className) cell.className = className; cell.textContent = value || ""; row.appendChild(cell); }
function renderReviewQueue() { const records = filteredRecords(); const totalPages = Math.max(1, Math.ceil(records.length / state.pageSize)); state.page = Math.min(state.page, totalPages); const start = (state.page - 1) * state.pageSize; const pageRecords = records.slice(start, start + state.pageSize); const body = document.getElementById("review-rows"); body.replaceChildren(); pageRecords.forEach((record) => { const row = document.createElement("tr"); appendCell(row, record.review_priority, "priority"); appendCell(row, record.function); appendCell(row, record.ticket_id); appendCell(row, record.candidate_status, "status"); appendCell(row, record.llm_root_cause_assessment, "summary"); appendCell(row, record.llm_fix_quality_assessment, "summary"); appendCell(row, [record.repair_plan_evidence, record.decision_evidence].filter(Boolean).join("\\n"), "evidence"); appendCell(row, record.no_fix_evidence, "evidence"); appendCell(row, record.evidence_index, "evidence"); appendCell(row, record.tree_rule_status, "status"); body.appendChild(row); }); document.getElementById("results").textContent = `${records.length} review candidates`; document.getElementById("page-status").textContent = `Page ${state.page} of ${totalPages}`; document.getElementById("previous-page").disabled = state.page === 1; document.getElementById("next-page").disabled = state.page === totalPages; }
functionFilter.addEventListener("input", () => { state.page = 1; renderReviewQueue(); });
priorityFilter.addEventListener("change", () => { state.page = 1; renderReviewQueue(); });
searchFilter.addEventListener("input", () => { state.page = 1; renderReviewQueue(); });
document.getElementById("previous-page").addEventListener("click", () => { state.page -= 1; renderReviewQueue(); });
document.getElementById("next-page").addEventListener("click", () => { state.page += 1; renderReviewQueue(); });
renderReviewQueue();
</script>
</body>
</html>
"""
    return (
        template.replace("__REVIEW_QUEUE__", review_queue_data)
        .replace("__SUMMARY__", summary_data)
        .replace("__SOURCE_WORKBOOK__", source_workbook_data)
    )


def export_phase06_rca_candidate_library(
    library: Mapping[str, Any],
    *,
    output_xlsx: str | Path,
    output_json: str | Path,
    output_html: str | Path,
    source_workbook_name: str,
) -> dict[str, Path]:
    """Write auditable candidate artifacts without materializing tree rules."""
    resolved_xlsx = Path(output_xlsx)
    resolved_json = Path(output_json)
    resolved_html = Path(output_html)
    for path in (resolved_xlsx, resolved_json, resolved_html):
        path.parent.mkdir(parents=True, exist_ok=True)

    workbook = Workbook()
    _write_overview_sheet(workbook, library, source_workbook_name)
    _write_record_sheet(workbook, "01_RCA_Review_Queue", library["review_queue"])
    _write_record_sheet(workbook, "02_All_Ticket_Audit", library["all_ticket_audit"])
    _write_function_readiness_sheet(workbook, library["function_readiness"])
    _write_data_dictionary_sheet(workbook)
    workbook.save(resolved_xlsx)
    workbook.close()

    payload = {
        "schema_version": "phase06-rca-candidate-library-v1",
        "source_workbook": source_workbook_name,
        "policy": {
            "llm_assessment_policy": (
                "Fingerprint-validated LLM assessments are review candidates, not verified "
                "RCA facts or repair instructions."
            ),
            "ticket_evidence_policy": (
                "Candidate review must inspect the raw Ticket evidence index and recorded "
                "remedy/decision evidence before making any technical claim."
            ),
            "tree_promotion_policy": (
                "Only an external documented human-review process may approve a candidate "
                "as a decision-tree branch."
            ),
            "automatic_tree_rule_count": 0,
        },
        "summary": library["summary"],
        "validation_audit": library["validation_audit"],
        "source_audit": library.get("source_audit", {}),
        "function_readiness": library["function_readiness"],
        "review_queue": library["review_queue"],
        "all_ticket_audit": library["all_ticket_audit"],
    }
    resolved_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    resolved_html.write_text(
        render_phase06_rca_candidate_review_html(library, source_workbook_name),
        encoding="utf-8",
    )
    return {
        "xlsx": resolved_xlsx,
        "json": resolved_json,
        "html": resolved_html,
    }


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a review-only Phase 06 RCA and remedy candidate library from "
            "fingerprint-validated LLM summaries and Ticket evidence."
        )
    )
    parser.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    parser.add_argument("--inputs", type=Path, default=DEFAULT_INPUTS)
    parser.add_argument("--summaries", type=Path, default=DEFAULT_SUMMARIES)
    parser.add_argument("--output-xlsx", type=Path, default=DEFAULT_OUTPUT_XLSX)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--output-html", type=Path, default=DEFAULT_OUTPUT_HTML)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    library = build_phase06_rca_candidate_library_from_sources(
        args.workbook, args.inputs, args.summaries
    )
    artifacts = export_phase06_rca_candidate_library(
        library,
        output_xlsx=args.output_xlsx,
        output_json=args.output_json,
        output_html=args.output_html,
        source_workbook_name=args.workbook.name,
    )
    summary = library["summary"]
    print(f"Audited Ticket rows: {summary['all_ticket_count']}")
    print(f"Validated LLM summaries: {summary['validated_llm_summary_count']}")
    print(f"P1 review candidates: {summary['p1_candidate_count']}")
    print(f"P2 review candidates: {summary['p2_candidate_count']}")
    print(f"Approved tree rules: {summary['approved_tree_rule_count']}")
    print(f"Saved Excel to: {artifacts['xlsx']}")
    print(f"Saved JSON to: {artifacts['json']}")
    print(f"Saved HTML to: {artifacts['html']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())