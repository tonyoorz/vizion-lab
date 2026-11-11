from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
import sqlite3
from typing import Mapping, Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from backend.analytics.phase09_postponed_v4 import _load_full_llm_ticket_ids
from backend.analytics.phase09_idcevo_report import _has_substantive_assessment_document


RELEASE_PATTERN = re.compile(
    r"(?<!\d)(?P<year>2[4-9])[-_/ ]?(?P<month>03|07|11)(?!\d)",
    re.IGNORECASE,
)
RELEASE_MONTH_INDEX = {3: 0, 7: 1, 11: 2}
CONFIRMED_TARGET_FIELDS = frozenset({"target_i_step_udf"})
STRUCTURED_PLAN_FIELDS = frozenset(
    {
        "target_set_udf",
        "target_week_udf",
        "supplier_confirmed_target_week_udf",
        "req_target_istep_udf",
        "requested_target_i_step_release_udf",
        "predicted_target_i_step_udf",
        "supplier_confirmed_i_step_udf",
        "closed_in_ver_udf",
        "sw_hrl_closed_version_udf",
        "app_version_udf",
    }
)
FIRST_USE_FIELD = "first_use_sop_of_function_udf"
EXPLICIT_PLAN_PATTERN = re.compile(
    r"\b(?:shift(?:ed)?\s+to|moved\s+to|push(?:ed)?\s+to|"
    r"planned\s+(?:for|in|with)|fix(?:ed)?\s+(?:is\s+)?planned\s+(?:for|in)|"
    r"will\s+be\s+(?:fixed|resolved|implemented)|"
    r"closed\s+in\s+version)\b",
    re.IGNORECASE,
)
WEAK_SIGNAL_PATTERN = re.compile(
    r"\b(?:postpon(?:e|ed|ement)|defer(?:red)?|tolerat(?:e|ed|ion)|shift)\b",
    re.IGNORECASE,
)
PROCESS_DOCUMENT_FIELD = "postpone_tolerate_doc_udf"
PROCESS_DECISION_DATE_FIELD = "postpone_toleratedate_udf"
PROCESS_TEXT_PATTERN = re.compile(
    r"\b(?:shift(?:ed)?\s+(?:to|from)|shift\s+to\s+set|"
    r"postpon(?:e|ed|ement)|tolerat(?:e|ed|ion)|tolerate\s+form|"
    r"toleration\s+form|defer(?:red)?)\b",
    re.IGNORECASE,
)
VERSION_PLAN_PATTERN = re.compile(
    r"\b(?:target\s+(?:i-step|istep|release|week|set)|"
    r"closed\s+in\s+version|first\s+use|sop|release\s+\d{2}[-/]?(?:03|07|11)|"
    r"\d{2}[-]?(?:03|07|11))\b",
    re.IGNORECASE,
)


def _text(value: object) -> str:
    return str(value or "").strip()


def _release(value: object) -> tuple[int, int] | None:
    matches = list(RELEASE_PATTERN.finditer(_text(value)))
    if not matches:
        return None
    match = matches[-1]
    return int(match.group("year")), int(match.group("month"))


def _release_index(release: tuple[int, int]) -> int:
    return release[0] * len(RELEASE_MONTH_INDEX) + RELEASE_MONTH_INDEX[release[1]]


def _release_label(release: tuple[int, int] | None) -> str:
    return "{}-{:02d}".format(release[0], release[1]) if release else ""


def _excerpt(value: object, maximum: int = 360) -> str:
    text = re.sub(r"\s+", " ", _text(value))
    return text if len(text) <= maximum else text[: maximum - 3].rstrip() + "..."


def _safe_excel_value(value: object) -> object:
    if not isinstance(value, str):
        return value
    return "'" + value if value[:1] in ("=", "+", "-", "@") else value


def _format_sheet(sheet, headers: Sequence[str], widths: Sequence[int]) -> None:
    for index in range(1, len(headers) + 1):
        cell = sheet.cell(row=1, column=index)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = "A1:{}{}".format(
        get_column_letter(len(headers)),
        sheet.max_row,
    )
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")


DETAIL_HEADERS = [
    "Ticket ID", "Cohort", "Current Phase", "Evidence Tier", "Evidence Basis",
    "Ticket Title", "Source Software", "Source PU/I-Step", "Target I-Step",
    "Target PU/I-Step", "Function Responsible", "Solution Cluster", "Matched Evidence Count",
]


def _detail_row(record: Mapping[str, object]) -> list[object]:
    matches = record.get("evidence_matches")
    return [
        record.get("ticket_id", ""), record.get("cohort", ""), record.get("current_phase", ""),
        record.get("evidence_tier", ""), record.get("evidence_basis", ""),
        record.get("title", ""), record.get("source_software_version", ""),
        record.get("source_release", ""), record.get("target_i_step", ""),
        record.get("target_release", ""), record.get("function_responsible", ""),
        record.get("solution_cluster", ""), len(matches) if isinstance(matches, list) else 0,
    ]


def _write_detail_sheet(sheet, records: Sequence[Mapping[str, object]]) -> None:
    sheet.append(DETAIL_HEADERS)
    for record in records:
        sheet.append([_safe_excel_value(value) for value in _detail_row(record)])
    _format_sheet(sheet, DETAIL_HEADERS, [14, 22, 24, 30, 84, 62, 58, 18, 30, 18, 34, 34, 20])


def write_wide_postponement_scan_excel(
    records: Sequence[Mapping[str, object]],
    *,
    output_path: str | Path,
    source_snapshot: str,
) -> Path:
    ordered_records = sorted(
        records,
        key=lambda item: (
            _text(item.get("evidence_tier")),
            _text(item.get("cohort")),
            _text(item.get("ticket_id")),
        ),
    )
    counts = Counter(_text(record.get("evidence_tier")) for record in ordered_records)
    workbook = Workbook()
    overview = workbook.active
    overview.title = "00_总览"
    overview.append(["Phase 09 + Open Wide Postponement Evidence Scan V4"])
    overview.append([])
    overview.append(["数据快照", source_snapshot or "Not recorded"])
    overview.append(["Candidate Tickets", len(ordered_records)])
    overview.append(["治理", "Evidence tiers indicate recorded plan signals, not verified repair or delivery completion."])
    overview.append([])
    overview.append(["Evidence Tier", "Ticket Count", "Interpretation"])
    interpretations = {
        "Confirmed delayed": "Target I-Step is strictly later than ticket-reported software.",
        "Structured future plan": "Structured target/week/version field is recorded but strict comparison is unavailable.",
        "Explicit text plan": "Description/comment/history explicitly records a future shift or fix plan.",
        "First Use/SOP context": "Future applicability context only; not repair delivery evidence.",
        "Weak signal - review": "Generic postpone/tolerate/defer signal; requires review.",
    }
    for tier, count in sorted(counts.items()):
        overview.append([tier, count, interpretations.get(tier, "")])
    overview.column_dimensions["A"].width = 34
    overview.column_dimensions["B"].width = 20
    overview.column_dimensions["C"].width = 100
    overview["A1"].font = Font(bold=True, size=14)

    all_sheet = workbook.create_sheet("01_宽口径候选")
    _write_detail_sheet(all_sheet, ordered_records)
    strong_sheet = workbook.create_sheet("02_强证据")
    _write_detail_sheet(
        strong_sheet,
        [
            record
            for record in ordered_records
            if _text(record.get("evidence_tier")) in {"Confirmed delayed", "Structured future plan"}
        ],
    )
    text_sheet = workbook.create_sheet("03_文本计划")
    _write_detail_sheet(
        text_sheet,
        [
            record
            for record in ordered_records
            if _text(record.get("evidence_tier")) in {"Explicit text plan", "First Use/SOP context"}
        ],
    )
    weak_sheet = workbook.create_sheet("04_弱信号复核")
    _write_detail_sheet(
        weak_sheet,
        [record for record in ordered_records if _text(record.get("evidence_tier")) == "Weak signal - review"],
    )

    evidence_sheet = workbook.create_sheet("08_命中证据")
    evidence_headers = ["Ticket ID", "Cohort", "Evidence Tier", "Evidence Source", "Field Name", "Matched Excerpt"]
    evidence_sheet.append(evidence_headers)
    for record in ordered_records:
        matches = record.get("evidence_matches")
        if not isinstance(matches, list):
            continue
        for match in matches:
            if not isinstance(match, Mapping):
                continue
            evidence_sheet.append(
                [
                    _safe_excel_value(_text(record.get("ticket_id"))),
                    _safe_excel_value(_text(record.get("cohort"))),
                    _safe_excel_value(_text(record.get("evidence_tier"))),
                    _safe_excel_value(_text(match.get("source"))),
                    _safe_excel_value(_text(match.get("field_name"))),
                    _safe_excel_value(_text(match.get("excerpt"))),
                ]
            )
    _format_sheet(evidence_sheet, evidence_headers, [14, 22, 30, 18, 34, 100])

    resolved_output_path = Path(output_path)
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(resolved_output_path)
    return resolved_output_path


PROCESS_HEADERS = [
    "Ticket ID", "Cohort", "Current Phase", "Process Tier", "Process Basis",
    "Ticket Title", "Source Software", "Target I-Step", "Function Responsible",
    "Solution Cluster", "Matched Process Evidence Count",
]


def _process_detail_row(record: Mapping[str, object]) -> list[object]:
    matches = record.get("process_matches")
    return [
        record.get("ticket_id", ""), record.get("cohort", ""), record.get("current_phase", ""),
        record.get("process_tier", ""), record.get("process_basis", ""),
        record.get("title", ""), record.get("source_software_version", ""),
        record.get("target_i_step", ""), record.get("function_responsible", ""),
        record.get("solution_cluster", ""), len(matches) if isinstance(matches, list) else 0,
    ]


def _write_process_sheet(sheet, records: Sequence[Mapping[str, object]]) -> None:
    sheet.append(PROCESS_HEADERS)
    for record in records:
        sheet.append([_safe_excel_value(value) for value in _process_detail_row(record)])
    _format_sheet(sheet, PROCESS_HEADERS, [14, 22, 24, 32, 84, 62, 58, 30, 34, 34, 28])


def write_shift_tolerate_process_excel(
    records: Sequence[Mapping[str, object]],
    *,
    output_path: str | Path,
    source_snapshot: str,
) -> Path:
    process_records = [
        record
        for record in records
        if _text(record.get("process_tier"))
        in {"Formal Shift/Tolerate process", "Explicit process + version"}
    ]
    process_records.sort(
        key=lambda item: (
            _text(item.get("process_tier")),
            _text(item.get("cohort")),
            _text(item.get("ticket_id")),
        )
    )
    counts = Counter(_text(record.get("process_tier")) for record in process_records)
    workbook = Workbook()
    overview = workbook.active
    overview.title = "00_总览"
    overview.append(["Phase 09 + Open Shift/Tolerate Process Evidence V4"])
    overview.append([])
    overview.append(["数据快照", source_snapshot or "Not recorded"])
    overview.append(["Process Candidates", len(process_records)])
    overview.append(["治理", "Historical process candidates only; lexical matches do not establish current postponement, approval, repair or delivery."])
    overview.append([])
    overview.append(["Process Tier", "Ticket Count", "Interpretation"])
    overview.append(["Formal Shift/Tolerate process", counts["Formal Shift/Tolerate process"], "Decision date or substantive postpone/tolerate document recorded."])
    overview.append(["Explicit process + version", counts["Explicit process + version"], "Lexical process/version co-occurrence; chronology, negation and ticket ownership require review."])
    overview.column_dimensions["A"].width = 38
    overview.column_dimensions["B"].width = 20
    overview.column_dimensions["C"].width = 100
    overview["A1"].font = Font(bold=True, size=14)

    all_sheet = workbook.create_sheet("01_流程候选")
    _write_process_sheet(all_sheet, process_records)
    formal_sheet = workbook.create_sheet("02_正式流程")
    _write_process_sheet(
        formal_sheet,
        [record for record in process_records if _text(record.get("process_tier")) == "Formal Shift/Tolerate process"],
    )
    explicit_sheet = workbook.create_sheet("03_文本流程")
    _write_process_sheet(
        explicit_sheet,
        [record for record in process_records if _text(record.get("process_tier")) == "Explicit process + version"],
    )

    evidence_sheet = workbook.create_sheet("08_流程证据")
    evidence_headers = ["Ticket ID", "Cohort", "Process Tier", "Evidence Source", "Field Name", "Matched Excerpt"]
    evidence_sheet.append(evidence_headers)
    for record in process_records:
        matches = record.get("process_matches")
        if not isinstance(matches, list):
            continue
        for match in matches:
            if not isinstance(match, Mapping):
                continue
            evidence_sheet.append(
                [
                    _safe_excel_value(_text(record.get("ticket_id"))),
                    _safe_excel_value(_text(record.get("cohort"))),
                    _safe_excel_value(_text(record.get("process_tier"))),
                    _safe_excel_value(_text(match.get("source"))),
                    _safe_excel_value(_text(match.get("field_name"))),
                    _safe_excel_value(_text(match.get("excerpt"))),
                ]
            )
    _format_sheet(evidence_sheet, evidence_headers, [14, 22, 32, 18, 34, 100])
    resolved_output_path = Path(output_path)
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(resolved_output_path)
    return resolved_output_path


def _comment_texts(comments: Sequence[object]) -> list[str]:
    values: list[str] = []
    for comment in comments:
        if isinstance(comment, Mapping):
            value = _text(comment.get("text") or comment.get("content") or comment.get("comment"))
        else:
            value = _text(comment)
        if value:
            values.append(value)
    return values


def _parsed_comments(value: object) -> list[object]:
    try:
        parsed = json.loads(_text(value) or "[]")
    except json.JSONDecodeError:
        return [_text(value)] if _text(value) else []
    return parsed if isinstance(parsed, list) else []


def classify_wide_postponement_evidence(
    *,
    source_software_version: object,
    history_events: Sequence[Mapping[str, object]],
    description: object,
    comments: Sequence[object],
) -> dict[str, object]:
    source_release = _release(source_software_version)
    target_value = ""
    target_release: tuple[int, int] | None = None
    structured_matches: list[dict[str, str]] = []
    first_use_matches: list[dict[str, str]] = []
    explicit_matches: list[dict[str, str]] = []
    weak_matches: list[dict[str, str]] = []

    for event in history_events:
        field_name = _text(event.get("field_name"))
        new_value = _text(event.get("new_value_text")) or _text(event.get("new_value"))
        old_value = _text(event.get("old_value_text")) or _text(event.get("old_value"))
        event_text = "{} {} {}".format(field_name, old_value, new_value)
        match = {
            "source": "History",
            "field_name": field_name,
            "excerpt": _excerpt(event_text),
        }
        if field_name in CONFIRMED_TARGET_FIELDS and new_value:
            target_value = new_value
            target_release = _release(new_value)
        if field_name in STRUCTURED_PLAN_FIELDS and new_value:
            structured_matches.append(match)
        if field_name == FIRST_USE_FIELD and new_value:
            first_use_matches.append(match)
        if EXPLICIT_PLAN_PATTERN.search(event_text):
            explicit_matches.append(match)
        elif WEAK_SIGNAL_PATTERN.search(event_text):
            weak_matches.append(match)

    text_sources = [("Description", _text(description))]
    text_sources.extend(("Comment", value) for value in _comment_texts(comments))
    for source, value in text_sources:
        if not value:
            continue
        match = {"source": source, "field_name": "", "excerpt": _excerpt(value)}
        if EXPLICIT_PLAN_PATTERN.search(value):
            explicit_matches.append(match)
        elif WEAK_SIGNAL_PATTERN.search(value):
            weak_matches.append(match)

    is_confirmed = (
        source_release is not None
        and target_release is not None
        and _release_index(target_release) > _release_index(source_release)
    )
    if is_confirmed:
        tier = "Confirmed delayed"
        basis = "Target I-Step {} is later than reported software {}.".format(
            _release_label(target_release),
            _release_label(source_release),
        )
        matches = [
            {
                "source": "History",
                "field_name": "target_i_step_udf",
                "excerpt": "Target I-Step: {}".format(target_value),
            }
        ]
    elif structured_matches:
        tier = "Structured future plan"
        basis = "Ticket history records a structured target, timing, or future-version field."
        matches = structured_matches
    elif explicit_matches:
        tier = "Explicit text plan"
        basis = "Description, comment, or history explicitly records a future shift or fix plan."
        matches = explicit_matches
    elif first_use_matches:
        tier = "First Use/SOP context"
        basis = "First Use/SOP records future applicability context; it is not delivery proof."
        matches = first_use_matches
    elif weak_matches:
        tier = "Weak signal - review"
        basis = "Text mentions postpone, tolerate, defer, or shift without a specific plan."
        matches = weak_matches
    else:
        tier = "No evidence"
        basis = "No configured postponed or future-plan evidence matched."
        matches = []
    return {
        "evidence_tier": tier,
        "evidence_basis": basis,
        "source_release": _release_label(source_release),
        "target_i_step": target_value,
        "target_release": _release_label(target_release),
        "evidence_matches": matches,
    }


def classify_shift_tolerate_process(
    *,
    history_events: Sequence[Mapping[str, object]],
    description: object,
    comments: Sequence[object],
) -> dict[str, object]:
    formal_matches: list[dict[str, str]] = []
    explicit_matches: list[dict[str, str]] = []
    for event in history_events:
        field_name = _text(event.get("field_name"))
        new_value = _text(event.get("new_value_text")) or _text(event.get("new_value"))
        old_value = _text(event.get("old_value_text")) or _text(event.get("old_value"))
        text = "{} {} {}".format(field_name, old_value, new_value)
        match = {
            "source": "History",
            "field_name": field_name,
            "excerpt": _excerpt(text),
        }
        if field_name == PROCESS_DECISION_DATE_FIELD and new_value:
            formal_matches.append(match)
        elif (
            field_name == PROCESS_DOCUMENT_FIELD
            and new_value
            and _has_substantive_assessment_document(new_value)
        ):
            formal_matches.append(match)
        if PROCESS_TEXT_PATTERN.search(text) and VERSION_PLAN_PATTERN.search(text):
            explicit_matches.append(match)

    text_sources = [("Description", _text(description))]
    text_sources.extend(("Comment", value) for value in _comment_texts(comments))
    for source, text in text_sources:
        if PROCESS_TEXT_PATTERN.search(text) and VERSION_PLAN_PATTERN.search(text):
            explicit_matches.append(
                {"source": source, "field_name": "", "excerpt": _excerpt(text)}
            )

    if formal_matches:
        return {
            "process_tier": "Formal Shift/Tolerate process",
            "process_basis": "Historical postponement/tolerance decision date or substantive process document; not current-state or approval proof.",
            "process_evidence_scope": "historical_candidate",
            "current_state_assessed": False,
            "human_review_required": True,
            "process_matches": formal_matches,
        }
    if explicit_matches:
        return {
            "process_tier": "Explicit process + version",
            "process_basis": "Lexical process/version co-occurrence only; chronology, negation, retraction and related-ticket attribution are not resolved.",
            "process_evidence_scope": "historical_candidate",
            "current_state_assessed": False,
            "human_review_required": True,
            "process_matches": explicit_matches,
        }
    return {
        "process_tier": "No process evidence",
        "process_basis": "No formal decision evidence or explicit process-plus-version text recorded.",
        "process_evidence_scope": "historical_candidate",
        "current_state_assessed": False,
        "human_review_required": True,
        "process_matches": [],
    }


def load_wide_postponement_scan_records(
    db_path: str | Path,
    baseline_full_llm_path: str | Path,
) -> list[dict[str, object]]:
    phase09_ids = set(_load_full_llm_ticket_ids(baseline_full_llm_path))
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    try:
        open_ids = {
            _text(row["defect_id"])
            for row in connection.execute(
                """
                SELECT defect_id
                FROM octane_defects
                WHERE substr(trim(coalesce(phase, '')), 1, 2) NOT IN ('06', '09')
                """
            )
            if _text(row["defect_id"])
        }
        scope_ids = sorted(phase09_ids | open_ids)
        records: list[dict[str, object]] = []
        for start_index in range(0, len(scope_ids), 900):
            batch = scope_ids[start_index : start_index + 900]
            placeholders = ",".join("?" for _ in batch)
            defect_rows = connection.execute(
                """
                SELECT defect_id, name, phase, software_version, function_responsible,
                       solution_cluster, product_areas, defect_category, assigned_ecu,
                       project, tproject, description, comments
                FROM octane_defects
                WHERE defect_id IN ({placeholders})
                """.format(placeholders=placeholders),
                tuple(batch),
            ).fetchall()
            history_rows = connection.execute(
                """
                SELECT defect_id, event_timestamp, user_name, field_name,
                       old_value, new_value, old_value_text, new_value_text,
                       entry_index, change_index
                FROM octane_defect_history_events
                WHERE defect_id IN ({placeholders})
                ORDER BY defect_id, event_timestamp, entry_index, change_index
                """.format(placeholders=placeholders),
                tuple(batch),
            ).fetchall()
            history_by_ticket: dict[str, list[dict[str, object]]] = {}
            for row in history_rows:
                history_by_ticket.setdefault(_text(row["defect_id"]), []).append(dict(row))
            for row in defect_rows:
                ticket_id = _text(row["defect_id"])
                classification = classify_wide_postponement_evidence(
                    source_software_version=row["software_version"],
                    history_events=history_by_ticket.get(ticket_id, []),
                    description=row["description"],
                    comments=_parsed_comments(row["comments"]),
                )
                if classification["evidence_tier"] == "No evidence":
                    continue
                records.append(
                    {
                        "ticket_id": ticket_id,
                        "cohort": "Phase 09 FullLLM" if ticket_id in phase09_ids else "Current open",
                        "current_phase": _text(row["phase"]),
                        "title": _text(row["name"]),
                        "source_software_version": _text(row["software_version"]),
                        "function_responsible": _text(row["function_responsible"]),
                        "solution_cluster": _text(row["solution_cluster"]),
                        "product_areas": _text(row["product_areas"]),
                        "defect_category": _text(row["defect_category"]),
                        "assigned_ecu": _text(row["assigned_ecu"]),
                        **classification,
                    }
                )
        return sorted(
            records,
            key=lambda item: (
                _text(item.get("evidence_tier")),
                _text(item.get("cohort")),
                _text(item.get("ticket_id")),
            ),
        )
    finally:
        connection.close()


def load_shift_tolerate_process_records(
    db_path: str | Path,
    baseline_full_llm_path: str | Path,
) -> list[dict[str, object]]:
    phase09_ids = set(_load_full_llm_ticket_ids(baseline_full_llm_path))
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    try:
        open_ids = {
            _text(row["defect_id"])
            for row in connection.execute(
                """
                SELECT defect_id
                FROM octane_defects
                WHERE substr(trim(coalesce(phase, '')), 1, 2) NOT IN ('06', '09')
                """
            )
            if _text(row["defect_id"])
        }
        scope_ids = sorted(phase09_ids | open_ids)
        records: list[dict[str, object]] = []
        for start_index in range(0, len(scope_ids), 900):
            batch = scope_ids[start_index : start_index + 900]
            placeholders = ",".join("?" for _ in batch)
            defect_rows = connection.execute(
                """
                SELECT defect_id, name, phase, software_version, function_responsible,
                       solution_cluster, product_areas, defect_category, assigned_ecu,
                       description, comments
                FROM octane_defects
                WHERE defect_id IN ({placeholders})
                """.format(placeholders=placeholders),
                tuple(batch),
            ).fetchall()
            history_rows = connection.execute(
                """
                SELECT defect_id, event_timestamp, user_name, field_name,
                       old_value, new_value, old_value_text, new_value_text,
                       entry_index, change_index
                FROM octane_defect_history_events
                WHERE defect_id IN ({placeholders})
                ORDER BY defect_id, event_timestamp, entry_index, change_index
                """.format(placeholders=placeholders),
                tuple(batch),
            ).fetchall()
            history_by_ticket: dict[str, list[dict[str, object]]] = {}
            for row in history_rows:
                history_by_ticket.setdefault(_text(row["defect_id"]), []).append(dict(row))
            for row in defect_rows:
                ticket_id = _text(row["defect_id"])
                classification = classify_shift_tolerate_process(
                    history_events=history_by_ticket.get(ticket_id, []),
                    description=row["description"],
                    comments=_parsed_comments(row["comments"]),
                )
                if classification["process_tier"] == "No process evidence":
                    continue
                records.append(
                    {
                        "ticket_id": ticket_id,
                        "cohort": "Phase 09 FullLLM" if ticket_id in phase09_ids else "Current open",
                        "current_phase": _text(row["phase"]),
                        "title": _text(row["name"]),
                        "source_software_version": _text(row["software_version"]),
                        "function_responsible": _text(row["function_responsible"]),
                        "solution_cluster": _text(row["solution_cluster"]),
                        "product_areas": _text(row["product_areas"]),
                        "defect_category": _text(row["defect_category"]),
                        "assigned_ecu": _text(row["assigned_ecu"]),
                        **classification,
                    }
                )
        return sorted(
            records,
            key=lambda item: (
                _text(item.get("process_tier")),
                _text(item.get("cohort")),
                _text(item.get("ticket_id")),
            ),
        )
    finally:
        connection.close()