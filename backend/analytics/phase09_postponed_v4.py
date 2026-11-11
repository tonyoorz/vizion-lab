from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import Mapping, Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from backend.analytics.open_shift_scenario_triple import load_open_shift_raw_evidence


RELEASE_PATTERN = re.compile(
    r"(?<!\d)(?P<year>2[4-9])[-_/ ]?(?P<month>03|07|11)(?!\d)",
    re.IGNORECASE,
)
RELEASE_MONTH_INDEX = {3: 0, 7: 1, 11: 2}
EVIDENCE_CHUNK_SIZE = 30_000
PHASE09_POSTPONED_V4_INPUT_VERSION = "phase09-postponed-v4-glm53-flash"
TARGET_VERSION_FIELD = "target_i_step_udf"
TARGET_WEEK_FIELD = "target_week_udf"


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


def _safe_excel_value(value: object) -> object:
    if not isinstance(value, str):
        return value
    return "'" + value if value[:1] in ("=", "+", "-", "@") else value


def _evidence_chunks(value: object) -> list[str]:
    text = _text(value)
    if not text:
        return [""]
    return [
        text[index : index + EVIDENCE_CHUNK_SIZE]
        for index in range(0, len(text), EVIDENCE_CHUNK_SIZE)
    ]


def build_phase09_postponed_records(
    records: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    selected: list[dict[str, object]] = []
    for raw_record in records:
        source_version = _text(raw_record.get("source_software_version"))
        target_i_step = _text(raw_record.get("target_i_step"))
        source_release = _release(source_version)
        target_release = _release(target_i_step)
        if source_release is None or target_release is None:
            continue
        if _release_index(target_release) <= _release_index(source_release):
            continue
        validation_count = int(raw_record.get("validation_release_ticket_count") or 0)
        selected.append(
            {
                **dict(raw_record),
                "source_software_version": source_version,
                "source_release": _release_label(source_release),
                "target_i_step": target_i_step,
                "target_release": _release_label(target_release),
                "validation_release_ticket_count": validation_count,
                "validation_evidence_status": (
                    "Release-level environment observed in {} Phase 06/07/08 tickets; no ticket-specific validation link recorded.".format(
                        validation_count
                    )
                    if validation_count
                    else "No Phase 06/07/08 source ticket observed at this target release; ticket-specific validation evidence is not recorded."
                ),
            }
        )
    return sorted(selected, key=lambda item: _text(item.get("ticket_id")))


def _bounded_text(value: object, maximum: int) -> str:
    text = _text(value)
    if len(text) <= maximum:
        return text
    head_length = int(maximum * 0.75)
    tail_length = maximum - head_length
    return "{} [source truncated] {}".format(
        text[:head_length].rstrip(),
        text[-tail_length:].lstrip(),
    )


def build_phase09_postponed_v4_inputs(
    records: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    inputs: list[dict[str, object]] = []
    for record in records:
        ticket_id = _text(record.get("ticket_id"))
        if not ticket_id:
            continue
        ticket = {
            "title": _text(record.get("title")),
            "phase": _text(record.get("phase")),
            "problem_evidence": _bounded_text(record.get("description"), 4_500)
            or "No ticket description retained.",
            "comments_evidence": _bounded_text(record.get("comments"), 4_500)
            or "No comment text retained.",
            "history_evidence": _bounded_text(record.get("history_evidence"), 4_500)
            or "No history evidence retained.",
            "source_software_version": _text(record.get("source_software_version")),
            "source_release": _text(record.get("source_release")),
            "target_i_step": _text(record.get("target_i_step")),
            "target_release": _text(record.get("target_release")),
            "target_recorded_at": _text(record.get("target_recorded_at")),
            "target_week": _text(record.get("target_week")),
            "validation_release_ticket_count": int(
                record.get("validation_release_ticket_count") or 0
            ),
            "validation_evidence_status": _text(
                record.get("validation_evidence_status")
            ),
            "phase09_scope": "Closed Phase 09 ticket with a target release later than reported software.",
        }
        fingerprint_payload = {
            "input_version": PHASE09_POSTPONED_V4_INPUT_VERSION,
            "ticket_id": ticket_id,
            "ticket": ticket,
        }
        inputs.append(
            {
                "ticket_id": ticket_id,
                "input_version": PHASE09_POSTPONED_V4_INPUT_VERSION,
                "input_fingerprint": sha256(
                    json.dumps(
                        fingerprint_payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
                "ticket": ticket,
            }
        )
    return inputs


def write_phase09_postponed_v4_inputs(
    records: Sequence[Mapping[str, object]],
    output_path: str | Path,
) -> list[dict[str, object]]:
    inputs = build_phase09_postponed_v4_inputs(records)
    resolved_output_path = Path(output_path)
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_output_path.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            for record in inputs
        ),
        encoding="utf-8",
    )
    return inputs


def _load_full_llm_ticket_ids(workbook_path: str | Path) -> list[str]:
    from openpyxl import load_workbook

    workbook = load_workbook(workbook_path, read_only=True, data_only=True)
    candidate_ids: list[str] = []
    largest_unique_count = -1
    try:
        for worksheet in workbook.worksheets:
            headers = [
                _text(cell.value)
                for cell in next(worksheet.iter_rows(min_row=1, max_row=1))
            ]
            if "Ticket ID" not in headers:
                continue
            ticket_index = headers.index("Ticket ID")
            ticket_ids = [
                _text(row[ticket_index])
                for row in worksheet.iter_rows(min_row=2, values_only=True)
                if ticket_index < len(row) and _text(row[ticket_index])
            ]
            unique_ids = list(dict.fromkeys(ticket_ids))
            if worksheet.title.startswith("06_") and unique_ids:
                return unique_ids
            if len(unique_ids) > largest_unique_count:
                candidate_ids = unique_ids
                largest_unique_count = len(unique_ids)
    finally:
        workbook.close()
    if not candidate_ids:
        raise ValueError("No Ticket ID detail sheet found in Phase 09 FullLLM workbook")
    return candidate_ids


def _latest_target_fields(
    connection: sqlite3.Connection,
    ticket_ids: Sequence[str],
) -> dict[str, dict[str, str]]:
    values: dict[str, dict[str, str]] = {}
    for start_index in range(0, len(ticket_ids), 900):
        batch = ticket_ids[start_index : start_index + 900]
        placeholders = ",".join("?" for _ in batch)
        rows = connection.execute(
            """
            SELECT defect_id, field_name, new_value, new_value_text,
                   event_timestamp, entry_index, change_index
            FROM octane_defect_history_events
            WHERE defect_id IN ({placeholders})
              AND field_name IN (?, ?)
            ORDER BY defect_id, field_name, event_timestamp, entry_index, change_index
            """.format(placeholders=placeholders),
            tuple(batch) + (TARGET_VERSION_FIELD, TARGET_WEEK_FIELD),
        ).fetchall()
        for row in rows:
            value = _text(row["new_value_text"]) or _text(row["new_value"])
            if not value:
                continue
            ticket_values = values.setdefault(_text(row["defect_id"]), {})
            ticket_values[_text(row["field_name"])] = value
            ticket_values[_text(row["field_name"]) + "_at"] = _text(
                row["event_timestamp"]
            )
    return values


def _validation_release_counts(connection: sqlite3.Connection) -> Counter[tuple[int, int]]:
    counts: Counter[tuple[int, int]] = Counter()
    rows = connection.execute(
        """
        SELECT software_version
        FROM octane_defects
        WHERE substr(trim(coalesce(phase, '')), 1, 2) IN ('06', '07', '08')
          AND upper(trim(coalesce(tproject, project, '')))='IDCEVO'
          AND trim(coalesce(software_version, '')) <> ''
        """
    ).fetchall()
    for row in rows:
        parsed = _release(row["software_version"])
        if parsed:
            counts[parsed] += 1
    return counts


def _evidence_text_by_ticket(
    raw_evidence: Sequence[Mapping[str, object]],
) -> dict[str, dict[str, str]]:
    grouped: dict[str, dict[str, list[str]]] = {}
    for item in raw_evidence:
        ticket_id = _text(item.get("ticket_id"))
        if not ticket_id:
            continue
        target = grouped.setdefault(ticket_id, {"description": [], "comments": [], "history": []})
        evidence_type = _text(item.get("evidence_type"))
        if evidence_type == "Description":
            target["description"].append(_text(item.get("content")))
        elif evidence_type == "Comment":
            actor = _text(item.get("actor"))
            timestamp = _text(item.get("timestamp"))
            content = _text(item.get("content"))
            target["comments"].append(
                "[{} | {}] {}".format(timestamp or "Not recorded", actor or "Not recorded", content)
            )
        elif evidence_type == "History":
            target["history"].append(
                "[{} | {} | {}] {} -> {}".format(
                    _text(item.get("timestamp")) or "Not recorded",
                    _text(item.get("actor")) or "Not recorded",
                    _text(item.get("field_name")) or "Not recorded",
                    _text(item.get("old_value")) or "(blank)",
                    _text(item.get("new_value")) or "(blank)",
                )
            )
    return {
        ticket_id: {
            "description": "\n".join(values["description"]),
            "comments": "\n".join(values["comments"]),
            "history_evidence": "\n".join(values["history"]),
        }
        for ticket_id, values in grouped.items()
    }


def load_phase09_postponed_v4_records(
    db_path: str | Path,
    baseline_full_llm_path: str | Path,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    ticket_ids = _load_full_llm_ticket_ids(baseline_full_llm_path)
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    try:
        validation_counts = _validation_release_counts(connection)
        target_fields = _latest_target_fields(connection, ticket_ids)
        raw_records: list[dict[str, object]] = []
        for start_index in range(0, len(ticket_ids), 900):
            batch = ticket_ids[start_index : start_index + 900]
            placeholders = ",".join("?" for _ in batch)
            rows = connection.execute(
                """
                SELECT defect_id, name, phase, software_version, function_responsible,
                       solution_cluster, product_areas, defect_category, assigned_ecu
                FROM octane_defects
                WHERE defect_id IN ({placeholders})
                """.format(placeholders=placeholders),
                tuple(batch),
            ).fetchall()
            for row in rows:
                ticket_id = _text(row["defect_id"])
                fields = target_fields.get(ticket_id, {})
                target_value = _text(fields.get(TARGET_VERSION_FIELD))
                target_release = _release(target_value)
                raw_records.append(
                    {
                        "ticket_id": ticket_id,
                        "title": _text(row["name"]),
                        "phase": _text(row["phase"]),
                        "source_software_version": _text(row["software_version"]),
                        "target_i_step": target_value,
                        "target_recorded_at": _text(fields.get(TARGET_VERSION_FIELD + "_at")),
                        "target_week": _text(fields.get(TARGET_WEEK_FIELD)),
                        "validation_release_ticket_count": validation_counts.get(target_release, 0) if target_release else 0,
                        "function_responsible": _text(row["function_responsible"]),
                        "solution_cluster": _text(row["solution_cluster"]),
                        "product_areas": _text(row["product_areas"]),
                        "defect_category": _text(row["defect_category"]),
                        "assigned_ecu": _text(row["assigned_ecu"]),
                    }
                )
    finally:
        connection.close()
    postponed = build_phase09_postponed_records(raw_records)
    raw_evidence = load_open_shift_raw_evidence(
        db_path,
        [_text(record.get("ticket_id")) for record in postponed],
    )
    evidence_text = _evidence_text_by_ticket(raw_evidence)
    for record in postponed:
        record.update(evidence_text.get(_text(record.get("ticket_id")), {}))
    return postponed, raw_evidence


def load_phase09_postponed_v4_summaries(
    inputs: Sequence[Mapping[str, object]],
    summaries_path: str | Path,
) -> dict[str, dict[str, object]]:
    expected = {
        _text(item.get("ticket_id")): _text(item.get("input_fingerprint"))
        for item in inputs
        if _text(item.get("ticket_id")) and _text(item.get("input_fingerprint"))
    }
    summaries: dict[str, dict[str, object]] = {}
    for line in Path(summaries_path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        ticket_id = _text(item.get("ticket_id"))
        if (
            _text(item.get("source")).casefold() != "llm"
            or _text(item.get("input_fingerprint")) != expected.get(ticket_id, "")
        ):
            continue
        required = (
            "issue_summary",
            "postponement_reason_analysis",
            "resolution_timing_analysis",
            "resolution_version_analysis",
            "review_focus",
        )
        if not all(_text(item.get(field)) for field in required):
            continue
        summaries[ticket_id] = dict(item)
    return summaries


def enrich_phase09_postponed_v4_records(
    records: Sequence[Mapping[str, object]],
    summaries: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]:
    enriched: list[dict[str, object]] = []
    for record in records:
        summary = summaries.get(_text(record.get("ticket_id")))
        if not summary:
            continue
        enriched.append(
            {
                **dict(record),
                "issue_summary": _text(summary.get("issue_summary")),
                "postponement_reason_analysis": _text(summary.get("postponement_reason_analysis")),
                "resolution_timing_analysis": _text(summary.get("resolution_timing_analysis")),
                "resolution_version_analysis": _text(summary.get("resolution_version_analysis")),
                "review_focus": _text(summary.get("review_focus")),
                "llm_model": _text(summary.get("model")),
            }
        )
    return enriched


def _format_sheet(
    sheet,
    headers: Sequence[str],
    widths: Sequence[int],
) -> None:
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
    "Ticket ID", "Ticket 标题", "Phase", "Source Software", "Source PU/I-Step",
    "Target I-Step", "Target PU/I-Step", "Target Recorded At", "Target Week",
    "Release-level Validation Ticket Count", "Validation Evidence Status",
    "Function Responsible", "Solution Cluster", "LLM 问题摘要", "LLM 延期原因分析",
    "LLM 解决时间分析", "LLM 解决版本分析", "LLM 待核实", "LLM Model",
]


def _detail_row(record: Mapping[str, object]) -> list[object]:
    return [
        record.get("ticket_id", ""), record.get("title", ""), record.get("phase", ""),
        record.get("source_software_version", ""), record.get("source_release", ""),
        record.get("target_i_step", ""), record.get("target_release", ""),
        record.get("target_recorded_at", ""), record.get("target_week", ""),
        record.get("validation_release_ticket_count", 0), record.get("validation_evidence_status", ""),
        record.get("function_responsible", ""), record.get("solution_cluster", ""),
        record.get("issue_summary", ""), record.get("postponement_reason_analysis", ""),
        record.get("resolution_timing_analysis", ""), record.get("resolution_version_analysis", ""),
        record.get("review_focus", ""), record.get("llm_model", ""),
    ]


def write_phase09_postponed_v4_excel(
    records: Sequence[Mapping[str, object]],
    *,
    raw_evidence: Sequence[Mapping[str, object]],
    output_path: str | Path,
    source_snapshot: str,
    model_label: str,
) -> Path:
    workbook = Workbook()
    overview = workbook.active
    overview.title = "00_总览"
    overview.append(["IDCEVO Phase 09 Postponed / Validation-Gap Analysis V4"])
    overview.append([])
    overview.append(["数据快照", source_snapshot or "Not recorded"])
    overview.append(["模型", model_label])
    overview.append(["Phase 09 postponed candidates", len(records)])
    overview.append(["Raw evidence rows", len(raw_evidence)])
    overview.append(["判定", "Target I-Step release strictly later than the ticket-reported software release."])
    overview.append(["验证边界", "Release-level environment observation is not ticket-specific repair validation."])
    overview.column_dimensions["A"].width = 42
    overview.column_dimensions["B"].width = 110
    overview["A1"].font = Font(bold=True, size=14)

    postponed = workbook.create_sheet("01_延期候选")
    postponed.append(DETAIL_HEADERS)
    for record in records:
        postponed.append(_detail_row(record))
    _format_sheet(postponed, DETAIL_HEADERS, [14, 60, 24, 58, 18, 30, 18, 24, 14, 18, 78, 34, 34, 60, 72, 72, 72, 60, 24])

    validation = workbook.create_sheet("02_验证证据")
    validation_headers = ["Target PU/I-Step", "Ticket Count", "Release-level Phase 06/07/08 Ticket Count", "Interpretation"]
    validation.append(validation_headers)
    by_target: dict[str, list[Mapping[str, object]]] = {}
    for record in records:
        by_target.setdefault(_text(record.get("target_release")), []).append(record)
    for target_release, members in sorted(by_target.items()):
        validation_count = max(int(item.get("validation_release_ticket_count") or 0) for item in members)
        validation.append(
            [
                target_release,
                len(members),
                validation_count,
                "Release observed elsewhere; ticket-specific validation link is still not recorded."
                if validation_count
                else "No same-release Phase 06/07/08 source ticket observed.",
            ]
        )
    _format_sheet(validation, validation_headers, [20, 16, 34, 90])

    details = workbook.create_sheet("06_全部明细")
    details.append(DETAIL_HEADERS)
    for record in records:
        details.append(_detail_row(record))
    _format_sheet(details, DETAIL_HEADERS, [14, 60, 24, 58, 18, 30, 18, 24, 14, 18, 78, 34, 34, 60, 72, 72, 72, 60, 24])

    evidence = workbook.create_sheet("08_证据原文")
    evidence_headers = ["Ticket ID", "Evidence Type", "Timestamp", "Actor", "Field Name", "Old Value", "New Value", "Evidence Content", "Chunk Index", "Chunk Count"]
    evidence.append(evidence_headers)
    for item in raw_evidence:
        chunks = _evidence_chunks(item.get("content"))
        for chunk_index, content in enumerate(chunks, start=1):
            evidence.append(
                [
                    _safe_excel_value(_text(item.get("ticket_id"))),
                    _safe_excel_value(_text(item.get("evidence_type"))),
                    _safe_excel_value(_text(item.get("timestamp"))),
                    _safe_excel_value(_text(item.get("actor"))),
                    _safe_excel_value(_text(item.get("field_name"))),
                    _safe_excel_value(_text(item.get("old_value"))),
                    _safe_excel_value(_text(item.get("new_value"))),
                    _safe_excel_value(content),
                    chunk_index,
                    len(chunks),
                ]
            )
    _format_sheet(evidence, evidence_headers, [14, 16, 24, 28, 34, 52, 52, 100, 14, 14])

    resolved_output_path = Path(output_path)
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(resolved_output_path)
    return resolved_output_path