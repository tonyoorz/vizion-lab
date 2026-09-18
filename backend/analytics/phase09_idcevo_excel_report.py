from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

from backend.analytics.phase09_idcevo_llm_summary import (
    build_phase09_llm_summary_input_records,
)
from backend.analytics.phase09_idcevo_report import build_phase09_idcevo_report_payload


SUMMARY_SHEET = "00_总览"
REASONS_SHEET = "01_拒绝原因"
FUNCTION_RISK_SHEET = "02_功能风险"
PRIORITY_SHEET = "03_优先复核"
SEVERE_SHEET = "04_严重度复核"
DIRECT_CLOSE_SHEET = "05_无证据直关"
DETAIL_SHEET = "06_全部明细"
METHODOLOGY_SHEET = "07_判定口径"
EVIDENCE_SHEET = "08_证据原文"

SHEET_NAMES = (
    SUMMARY_SHEET,
    REASONS_SHEET,
    FUNCTION_RISK_SHEET,
    PRIORITY_SHEET,
    SEVERE_SHEET,
    DIRECT_CLOSE_SHEET,
    DETAIL_SHEET,
    METHODOLOGY_SHEET,
    EVIDENCE_SHEET,
)

NAVY_FILL = PatternFill("solid", fgColor="18365D")
BLUE_FILL = PatternFill("solid", fgColor="D9EAF7")
GREEN_FILL = PatternFill("solid", fgColor="D9EAD3")
AMBER_FILL = PatternFill("solid", fgColor="FCE4D6")
RED_FILL = PatternFill("solid", fgColor="F4CCCC")
HEADER_FONT = Font(color="FFFFFF", bold=True)
TITLE_FONT = Font(color="FFFFFF", bold=True, size=16)
HEADER_ALIGNMENT = Alignment(horizontal="center", vertical="center", wrap_text=True)
BODY_ALIGNMENT = Alignment(vertical="top", wrap_text=True)

DETAIL_HEADERS = (
    "Ticket ID",
    "功能",
    "Solution Cluster",
    "Defect Category",
    "Product Areas",
    "Function2Module",
    "Function Responsible",
    "Assigned ECU",
    "原始项目（project）",
    "最终项目（tproject）",
    "Software Version",
    "Problem Finder Team / fallback",
    "创建时间",
    "关闭处置",
    "LLM 处理摘要",
    "摘要来源",
    "风险等级依据",
    "证据原文索引",
    "风险层级",
    "风险分",
    "QGate 严重",
    "需闭环复核",
    "关闭原因",
    "原因来源",
    "拒绝路径",
    "直接 01->09",
    "09 后重开",
    "09 后进入 06",
    "后续跟进状态",
    "Target 版本状态",
    "Target 版本",
    "计划窗口状态",
    "计划窗口",
    "商业解决日期状态",
    "商业解决日期",
    "Reprel / Shift",
    "延期文档状态",
    "延期/容忍登记日期",
    "关联解决状态",
    "客户现场风险判断",
    "风险标记",
    "Ticket 标题",
    "问题描述摘录",
    "修复计划证据",
    "决策证据",
    "无修复证据",
    "延期文档摘录",
)

DETAIL_WIDTHS = (
    16,
    28,
    26,
    32,
    48,
    20,
    26,
    22,
    18,
    18,
    38,
    28,
    20,
    42,
    88,
    22,
    82,
    48,
    30,
    10,
    13,
    14,
    28,
    24,
    20,
    14,
    13,
    16,
    35,
    34,
    24,
    34,
    22,
    38,
    26,
    22,
    25,
    26,
    45,
    48,
    44,
    58,
    64,
    64,
    64,
    64,
    64,
)

EVIDENCE_HEADERS = (
    "Ticket ID",
    "证据类型",
    "证据序号",
    "分块序号",
    "分块总数",
    "时间",
    "作者/操作者",
    "来源字段",
    "原文内容",
    "用途",
)

EVIDENCE_WIDTHS = (16, 20, 12, 12, 12, 28, 28, 48, 110, 50)
EVIDENCE_TEXT_CHUNK_SIZE = 30_000
REQUIRED_PROCESSING_SUMMARY_LABELS = (
    "问题：",
    "处理过程：",
    "Reject/关闭原因：",
    "后续闭环：",
    "风险判断与待核实：",
)
HEX_DIAGNOSTIC_PATTERN = re.compile(r"\b0x[0-9a-f]{3,}\b", re.IGNORECASE)


def _text(value: object, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _cell_value(value: object) -> object:
    if isinstance(value, bool):
        return "是" if value else "否"
    if value is None:
        return ""
    text = str(value)
    if len(text) <= 32_767:
        return value
    suffix = "\n[Truncated: source text exceeds Excel's 32,767-character cell limit.]"
    return f"{text[:32_767 - len(suffix)]}{suffix}"


def _tickets(payload: Mapping[str, object]) -> list[dict[str, object]]:
    raw_tickets = payload.get("tickets")
    if not isinstance(raw_tickets, list):
        return []
    return [dict(ticket) for ticket in raw_tickets if isinstance(ticket, dict)]


def _is_valid_llm_processing_summary(value: object) -> bool:
    summary = _text(value)
    normalized_summary = summary.casefold()
    return (
        bool(summary)
        and all(label in summary for label in REQUIRED_PROCESSING_SUMMARY_LABELS)
        and "<html" not in normalized_summary
        and "end of error description" not in normalized_summary
    )


def _summary_safe_title(value: object) -> str:
    return HEX_DIAGNOSTIC_PATTERN.sub(
        "[diagnostic code]", _text(value, "Not recorded")
    )


def _load_llm_summaries(
    path: str | Path | None,
    *,
    expected_inputs_by_ticket_id: Mapping[str, Mapping[str, object]],
) -> dict[str, dict[str, str]]:
    if path is None:
        return {}

    summary_path = Path(path)
    if not summary_path.is_file():
        raise FileNotFoundError(f"LLM summary file does not exist: {summary_path}")

    summaries: dict[str, dict[str, str]] = {}
    for line_number, line in enumerate(
        summary_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            raw_summary = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"Invalid JSONL summary record at {summary_path}:{line_number}"
            ) from error
        if not isinstance(raw_summary, Mapping):
            continue
        ticket_id = _text(raw_summary.get("ticket_id"))
        summary = _text(raw_summary.get("summary"))
        expected_input = expected_inputs_by_ticket_id.get(ticket_id)
        if (
            not ticket_id
            or not _is_valid_llm_processing_summary(summary)
            or expected_input is None
            or _text(raw_summary.get("source")).casefold() != "llm"
            or _text(raw_summary.get("input_fingerprint"))
            != _text(expected_input.get("input_fingerprint"))
            or _text(raw_summary.get("input_version"))
            != _text(expected_input.get("input_version"))
        ):
            continue
        summaries[ticket_id] = {
            "summary": summary,
            "model": _text(raw_summary.get("model")),
            "source": _text(raw_summary.get("source"), "llm"),
        }
    return summaries


def _fallback_processing_summary(ticket: Mapping[str, object]) -> str:
    return "\n".join(
        (
            "问题：{}。".format(_summary_safe_title(ticket.get("name"))),
            "处理过程：直接 01 -> 09：{}；09 后重开：{}；09 后进入 06：{}。".format(
                "是" if bool(ticket.get("direct_close")) else "否",
                "是" if bool(ticket.get("reopened_after_phase09")) else "否",
                "是" if bool(ticket.get("entered_phase06_after_phase09")) else "否",
            ),
            "Reject/关闭原因：{}（来源：{}；拒绝路径：{}）。".format(
                _text(ticket.get("effective_reason"), "Not recorded"),
                _text(ticket.get("reason_source"), "Not recorded"),
                _text(ticket.get("rejection_route"), "Not recorded"),
            ),
            "后续闭环：{}；Target：{}；计划窗口：{}；商业解决日期：{}；{}；关联解决：{}。".format(
                _text(ticket.get("follow_up_status"), "Not recorded"),
                _text(ticket.get("target_version_value"), "Not recorded"),
                _text(ticket.get("planning_window_value"), "Not recorded"),
                _text(ticket.get("commercial_solution_date_value"), "Not recorded"),
                _text(ticket.get("reprel_change_status"), "Not recorded"),
                _text(ticket.get("related_resolution_status"), "Not recorded"),
            ),
            "风险判断与待核实：{}（风险分：{}）。{}。LLM 问题/过程摘要待补全；完整原文见 08_证据原文。".format(
                _text(ticket.get("risk_tier"), "Not recorded"),
                _text(ticket.get("risk_score"), "Not recorded"),
                _text(ticket.get("production_exposure_status"), "Not recorded"),
            ),
        )
    )


def _apply_llm_summaries(
    tickets: Sequence[Mapping[str, object]],
    summaries_by_ticket_id: Mapping[str, Mapping[str, str]],
) -> list[dict[str, object]]:
    enriched_tickets: list[dict[str, object]] = []
    for ticket in tickets:
        enriched_ticket = dict(ticket)
        ticket_id = _text(enriched_ticket.get("ticket_id"))
        summary = summaries_by_ticket_id.get(ticket_id)
        if summary is None:
            enriched_ticket["llm_processing_summary"] = _fallback_processing_summary(
                enriched_ticket
            )
            enriched_ticket["llm_summary_source"] = "规则摘要 / LLM待生成"
        else:
            enriched_ticket["llm_processing_summary"] = summary["summary"]
            model = _text(summary.get("model"), "configured model")
            source = _text(summary.get("source"), "llm").upper()
            enriched_ticket["llm_summary_source"] = f"{source} / {model}"
        enriched_tickets.append(enriched_ticket)
    return enriched_tickets


def _function_name(ticket: Mapping[str, object]) -> str:
    solution_cluster = _text(ticket.get("solution_cluster"))
    if solution_cluster and solution_cluster != "(blank)":
        return solution_cluster
    defect_category = _text(ticket.get("defect_category"))
    if defect_category and defect_category != "(blank)":
        return defect_category
    product_areas = _text(ticket.get("product_areas"))
    if product_areas and product_areas != "(blank)":
        return product_areas
    function2modul = _text(ticket.get("function2modul"))
    if function2modul and function2modul != "(blank)":
        return function2modul
    assigned_ecu = _text(ticket.get("assigned_ecu"))
    if assigned_ecu and assigned_ecu != "(blank)":
        return assigned_ecu
    return "(未记录功能)"


def _detail_rows(tickets: Sequence[Mapping[str, object]]) -> list[list[object]]:
    rows: list[list[object]] = []
    for ticket in tickets:
        risk_flags = ticket.get("risk_flags")
        flag_text = " | ".join(str(flag) for flag in risk_flags) if isinstance(risk_flags, list) else ""
        rows.append(
            [
                ticket.get("ticket_id"),
                _function_name(ticket),
                ticket.get("solution_cluster"),
                ticket.get("defect_category"),
                ticket.get("product_areas"),
                ticket.get("function2modul"),
                ticket.get("function_responsible"),
                ticket.get("assigned_ecu"),
                ticket.get("legacy_project"),
                ticket.get("final_project"),
                ticket.get("software_version"),
                ticket.get("team"),
                ticket.get("creation_time"),
                ticket.get("closure_disposition"),
                ticket.get("llm_processing_summary"),
                ticket.get("llm_summary_source"),
                ticket.get("risk_basis"),
                ticket.get("evidence_index"),
                ticket.get("risk_tier"),
                ticket.get("risk_score"),
                ticket.get("is_governed_severe"),
                ticket.get("needs_closure_evidence_review"),
                ticket.get("effective_reason"),
                ticket.get("reason_source"),
                ticket.get("rejection_route"),
                ticket.get("direct_close"),
                ticket.get("reopened_after_phase09"),
                ticket.get("entered_phase06_after_phase09"),
                ticket.get("follow_up_status"),
                ticket.get("target_version_status"),
                ticket.get("target_version_value"),
                ticket.get("planning_window_status"),
                ticket.get("planning_window_value"),
                ticket.get("commercial_solution_date_status"),
                ticket.get("commercial_solution_date_value"),
                ticket.get("reprel_change_status"),
                ticket.get("postponement_document_status"),
                ticket.get("postponement_decision_date_value"),
                ticket.get("related_resolution_status"),
                ticket.get("production_exposure_status"),
                flag_text,
                ticket.get("name"),
                ticket.get("description_excerpt"),
                ticket.get("plan_evidence_excerpt"),
                ticket.get("decision_evidence_excerpt"),
                ticket.get("no_fix_evidence_excerpt"),
                ticket.get("postponement_document_excerpt"),
            ]
        )
    return rows


def _raw_text(value: object) -> str:
    return "" if value is None else str(value)


def _split_evidence_text(value: object) -> list[str]:
    text = _raw_text(value)
    if not text:
        return []
    return [
        text[start_index : start_index + EVIDENCE_TEXT_CHUNK_SIZE]
        for start_index in range(0, len(text), EVIDENCE_TEXT_CHUNK_SIZE)
    ]


def _join_distinct(values: Sequence[object]) -> str:
    distinct_values: list[str] = []
    for value in values:
        text = _text(value)
        if text and text not in distinct_values:
            distinct_values.append(text)
    return " | ".join(distinct_values)


def _comment_evidence_text(ticket: Mapping[str, object]) -> tuple[str, str]:
    raw_records = ticket.get("comment_records")
    if not isinstance(raw_records, list):
        return "", ""
    entries: list[str] = []
    authors: list[str] = []
    for sequence, raw_record in enumerate(raw_records, start=1):
        if not isinstance(raw_record, Mapping):
            continue
        text = _raw_text(raw_record.get("raw_text"))
        if not text:
            continue
        timestamp = _text(raw_record.get("timestamp"), "Not recorded")
        author = _text(raw_record.get("author"), "Not recorded")
        comment_id = _text(raw_record.get("comment_id"), "Not recorded")
        authors.append(author)
        entries.append(
            "[Comment {} | id: {} | timestamp: {} | author: {}]\n{}".format(
                sequence,
                comment_id,
                timestamp,
                author,
                text,
            )
        )
    return "\n\n".join(entries), _join_distinct(authors)


def _history_evidence_text(ticket: Mapping[str, object]) -> tuple[str, str, str]:
    raw_events = ticket.get("audit_history_events")
    if not isinstance(raw_events, list):
        return "", "", ""
    entries: list[str] = []
    actors: list[str] = []
    fields: list[str] = []
    for sequence, raw_event in enumerate(raw_events, start=1):
        if not isinstance(raw_event, Mapping):
            continue
        timestamp = _text(raw_event.get("timestamp"), "Not recorded")
        actor = _text(raw_event.get("actor"), "Not recorded")
        field_name = _text(raw_event.get("field_name"), "Not recorded")
        old_value = _raw_text(raw_event.get("old_value"))
        new_value = _raw_text(raw_event.get("new_value"))
        actors.append(actor)
        fields.append(field_name)
        entries.append(
            "[Event {} | timestamp: {} | actor: {} | field: {}]\nOld value:\n{}\nNew value:\n{}".format(
                sequence,
                timestamp,
                actor,
                field_name,
                old_value,
                new_value,
            )
        )
    return "\n\n".join(entries), _join_distinct(actors), _join_distinct(fields)


def _ticket_evidence_entries(ticket: Mapping[str, object]) -> list[tuple[str, str, str, str, str]]:
    entries: list[tuple[str, str, str, str, str]] = []
    description = _raw_text(ticket.get("description_full"))
    if description:
        entries.append(
            (
                "Ticket description",
                _text(ticket.get("creation_time")),
                "",
                "octane_defects.description",
                description,
            )
        )

    comment_text, comment_authors = _comment_evidence_text(ticket)
    if comment_text:
        entries.append(
            (
                "Ticket comment",
                "Multiple comments; see original text",
                comment_authors,
                "octane_defects.comments",
                comment_text,
            )
        )

    history_text, history_actors, history_fields = _history_evidence_text(ticket)
    if history_text:
        entries.append(
            (
                "Closure history",
                "Multiple events; see original text",
                history_actors,
                history_fields,
                history_text,
            )
        )
    return entries


def _write_evidence_sheet(sheet: Any, tickets: Sequence[Mapping[str, object]]) -> None:
    for column_index, header in enumerate(EVIDENCE_HEADERS, start=1):
        cell = sheet.cell(1, column_index, header)
        cell.fill = NAVY_FILL
        cell.font = HEADER_FONT
        cell.alignment = HEADER_ALIGNMENT

    row_index = 2
    for ticket in tickets:
        ticket_id = _text(ticket.get("ticket_id"))
        for evidence_sequence, (evidence_type, timestamp, actor, source_field, content) in enumerate(
            _ticket_evidence_entries(ticket), start=1
        ):
            chunks = _split_evidence_text(content)
            for chunk_sequence, chunk in enumerate(chunks, start=1):
                values = (
                    ticket_id,
                    evidence_type,
                    evidence_sequence,
                    chunk_sequence,
                    len(chunks),
                    timestamp,
                    actor,
                    source_field,
                    chunk,
                    "完整原文；按 Ticket ID、证据类型和分块序号重组。",
                )
                for column_index, value in enumerate(values, start=1):
                    cell = sheet.cell(row_index, column_index, _cell_value(value))
                    if isinstance(cell.value, str):
                        cell.alignment = BODY_ALIGNMENT
                row_index += 1

    _set_widths(sheet, EVIDENCE_WIDTHS)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = "A1:{}{}".format(
        get_column_letter(len(EVIDENCE_HEADERS)), max(row_index - 1, 1)
    )


def _set_widths(sheet: Any, widths: Sequence[float]) -> None:
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width


def _write_table(
    sheet: Any,
    *,
    start_row: int,
    headers: Sequence[str],
    rows: Sequence[Sequence[object]],
    widths: Sequence[float],
    table_name: str,
) -> None:
    for column_index, header in enumerate(headers, start=1):
        cell = sheet.cell(start_row, column_index, header)
        cell.fill = NAVY_FILL
        cell.font = HEADER_FONT
        cell.alignment = HEADER_ALIGNMENT

    for row_index, row in enumerate(rows, start=start_row + 1):
        for column_index, value in enumerate(row, start=1):
            cell = sheet.cell(row_index, column_index, _cell_value(value))
            if isinstance(cell.value, str):
                cell.alignment = BODY_ALIGNMENT

    _set_widths(sheet, widths)
    last_column = get_column_letter(len(headers))
    last_row = start_row + len(rows)
    sheet.auto_filter.ref = f"A{start_row}:{last_column}{max(last_row, start_row)}"
    if rows:
        table = Table(displayName=table_name, ref=f"A{start_row}:{last_column}{last_row}")
        table.tableStyleInfo = TableStyleInfo(
            name="TableStyleMedium2",
            showFirstColumn=False,
            showLastColumn=False,
            showRowStripes=True,
            showColumnStripes=False,
        )
        sheet.add_table(table)


def _color_detail_risk_cells(sheet: Any) -> None:
    header_index = {
        _text(cell.value): column_index
        for column_index, cell in enumerate(sheet[1], start=1)
    }
    disposition_column = header_index.get("关闭处置")
    risk_column = header_index.get("风险层级")
    severe_column = header_index.get("QGate 严重")
    for row_index in range(2, sheet.max_row + 1):
        disposition = _text(
            sheet.cell(row_index, disposition_column).value if disposition_column else ""
        )
        risk_tier = _text(sheet.cell(row_index, risk_column).value if risk_column else "")
        governed_severe = _text(
            sheet.cell(row_index, severe_column).value if severe_column else "")
        fill = None
        if risk_tier == "Priority production-exposure review":
            fill = RED_FILL
        elif governed_severe == "是":
            fill = AMBER_FILL
        elif disposition == "Direct closure without auditable rationale or follow-up":
            fill = AMBER_FILL
        if fill is not None:
            for column_index in (disposition_column, risk_column, severe_column):
                if column_index:
                    sheet.cell(row_index, column_index).fill = fill


def _write_summary(sheet: Any, payload: Mapping[str, object]) -> None:
    source = payload.get("source")
    source_data = source if isinstance(source, Mapping) else {}
    summary = payload.get("summary")
    summary_data = summary if isinstance(summary, Mapping) else {}
    distributions = payload.get("distributions")
    distribution_data = distributions if isinstance(distributions, Mapping) else {}

    sheet.merge_cells("A1:H2")
    sheet.merge_cells("A3:H3")
    sheet["A1"] = _text(source_data.get("report_title"), "IDCEVO Phase 09 Closure Risk Review")
    sheet["A3"] = _text(source_data.get("scope_description"))
    sheet["A5"] = "Source snapshot"
    sheet["B5"] = _text(source_data.get("latest_fetched_at"))
    sheet["D5"] = "Latest modification"
    sheet["E5"] = _text(source_data.get("latest_last_modified"))
    sheet["A6"] = "Scope rule"
    sheet.merge_cells("B6:H6")
    sheet["B6"] = _text(source_data.get("scope_rule"))
    sheet["A1"].fill = NAVY_FILL
    sheet["A1"].font = TITLE_FONT
    sheet["A1"].alignment = Alignment(horizontal="center", vertical="center")
    sheet["A3"].fill = BLUE_FILL
    sheet["A3"].alignment = BODY_ALIGNMENT

    metrics = [
        (
            "Analyzed final IDCEVO current Phase 09",
            summary_data.get("analyzed_phase09_total"),
            "Uses final tproject; all China-specific functions are included and only CoC Problem Finder Teams are excluded.",
        ),
        (
            "All current IDCEVO Phase 09",
            summary_data.get("idcevo_current_phase09_total"),
            "Population before the finder-team exclusion.",
        ),
        (
            "CoC finder-team excluded",
            summary_data.get("coc_development_team_excluded_total"),
            "Excluded only when Problem Finder Team contains CoC.",
        ),
        (
            "China-specific in analyzed scope",
            summary_data.get("china_specific_in_scope_total"),
            "Retained after the CoC Problem Finder Team exclusion because the requested scope is all IDCEVO.",
        ),
        (
            "Direct lifecycle 01 -> 09",
            summary_data.get("direct_close_total"),
            "A process audit signal, not a risk verdict by itself.",
        ),
        (
            "Acceptable closure evidence",
            summary_data.get("normal_closure_total"),
            "Benign reason without elevated evidence, or duplicate linked to a resolved parent.",
        ),
        (
            "Follow-up / remediation recorded",
            next(
                (
                    item.get("count")
                    for item in distribution_data.get("closure_dispositions", [])
                    if isinstance(item, Mapping)
                    and item.get("label") == "Follow-up / remediation recorded"
                ),
                0,
            ),
            "Target, repair plan, commercial solution date, linked resolution, or Reprel Shift exists. This is not proof of a delivered fix.",
        ),
        (
            "Direct closure without auditable rationale or follow-up",
            summary_data.get("needs_closure_evidence_review_total"),
            "Primary review queue: no benign closure, no follow-up evidence, and no substantive assessment document.",
        ),
        (
            "Governed severe in primary review queue",
            summary_data.get("needs_closure_evidence_review_governed_severe_total"),
            "Showstopper Candidate/Confirmed plus BI 04/05/06.",
        ),
        (
            "Priority production-exposure review",
            summary_data.get("priority_review_total"),
            "Human review candidates; the snapshot does not prove a field escape.",
        ),
    ]
    _write_table(
        sheet,
        start_row=8,
        headers=("指标", "数量", "解读"),
        rows=metrics,
        widths=(48, 16, 104),
        table_name="Phase09SummaryMetrics",
    )
    sheet.freeze_panes = "A9"
    for coordinate in ("A5", "D5", "A6"):
        sheet[coordinate].fill = NAVY_FILL
        sheet[coordinate].font = HEADER_FONT
        sheet[coordinate].alignment = HEADER_ALIGNMENT
    sheet["B6"].alignment = BODY_ALIGNMENT


def _reason_interpretation(reason_group: str) -> str:
    interpretations = {
        "Duplicate / linked ticket": "Acceptable only when the explicit parent link reaches Phase 06; otherwise keep it in review.",
        "Expected behaviour / test-quality": "Potentially acceptable only without elevated customer or Showstopper evidence.",
        "Not reproducible / insufficient evidence": "Not automatically safe; validate test evidence and repeatability when impact is elevated.",
        "Decision / tolerance / not implemented": "Risk acceptance or deferral, not a delivered solution. Require a target, Shift, or decision evidence.",
        "External handling / ownership": "A handover does not itself prove closure; verify the external handling record.",
        "No closure reason recorded": "No structured rationale. Combine with document, comment, target, and linked-resolution evidence before accepting closure.",
    }
    return interpretations.get(reason_group, "Review the original rationale and closure evidence.")


def _write_reasons(sheet: Any, payload: Mapping[str, object]) -> None:
    distributions = payload.get("distributions")
    distribution_data = distributions if isinstance(distributions, Mapping) else {}
    reason_group_rows = [
        [
            _text(item.get("label")),
            item.get("count"),
            _reason_interpretation(_text(item.get("label"))),
        ]
        for item in distribution_data.get("blocking_reason_groups", [])
        if isinstance(item, Mapping)
    ]
    _write_table(
        sheet,
        start_row=1,
        headers=("关闭原因分组", "Ticket 数", "审计解读"),
        rows=reason_group_rows,
        widths=(42, 14, 100),
        table_name="ClosureReasonGroups",
    )
    detail_start_row = len(reason_group_rows) + 4
    effective_reason_rows = [
        [_text(item.get("label")), item.get("count")]
        for item in distribution_data.get("effective_reasons", [])
        if isinstance(item, Mapping)
    ]
    _write_table(
        sheet,
        start_row=detail_start_row,
        headers=("Effective closure reason", "Ticket 数"),
        rows=effective_reason_rows,
        widths=(56, 16),
        table_name="EffectiveClosureReasons",
    )
    sheet.freeze_panes = "A2"


def _function_risk_rows(tickets: Sequence[Mapping[str, object]]) -> list[list[object]]:
    counters: dict[str, Counter[str]] = defaultdict(Counter)
    for ticket in tickets:
        function_name = _function_name(ticket)
        counter = counters[function_name]
        counter["total"] += 1
        if ticket.get("closure_disposition") == "Acceptable closure evidence":
            counter["acceptable"] += 1
        if ticket.get("closure_disposition") == "Follow-up / remediation recorded":
            counter["follow_up"] += 1
        if bool(ticket.get("needs_closure_evidence_review")):
            counter["needs_review"] += 1
        if bool(ticket.get("needs_closure_evidence_review")) and bool(
            ticket.get("is_governed_severe")
        ):
            counter["severe_needs_review"] += 1
        if bool(ticket.get("needs_closure_evidence_review")) and ticket.get(
            "risk_tier"
        ) == "Priority production-exposure review":
            counter["priority_needs_review"] += 1
        if ticket.get("follow_up_status") == "No remediation or follow-up evidence":
            counter["no_follow_up"] += 1
        if ticket.get("postponement_document_status") == "Template only":
            counter["template_only"] += 1
        if ticket.get("postponement_document_status") == "Substantive assessment recorded":
            counter["substantive_document"] += 1

    rows: list[list[object]] = []
    for function_name, counter in counters.items():
        if counter["priority_needs_review"]:
            risk_level = "最高优先"
            risk_summary = "存在无可审计闭环的优先生产/客户现场复核票。"
        elif counter["severe_needs_review"]:
            risk_level = "高"
            risk_summary = "存在 Showstopper + BI04/05/06 的无可审计闭环票。"
        elif counter["needs_review"]:
            risk_level = "中"
            risk_summary = "存在直接关闭且没有可审计理由、跟进或实质评估的票。"
        else:
            risk_level = "观察"
            risk_summary = "未发现主复核队列票；仍应结合版本和现场信息确认。"
        rows.append(
            [
                function_name,
                counter["total"],
                counter["acceptable"],
                counter["follow_up"],
                counter["needs_review"],
                counter["severe_needs_review"],
                counter["priority_needs_review"],
                counter["no_follow_up"],
                counter["template_only"],
                counter["substantive_document"],
                risk_level,
                risk_summary,
            ]
        )
    return sorted(
        rows,
        key=lambda row: (-int(row[6]), -int(row[5]), -int(row[4]), -int(row[1]), str(row[0])),
    )


def _write_function_risk(sheet: Any, tickets: Sequence[Mapping[str, object]]) -> None:
    headers = (
        "功能（Solution Cluster / Defect Category / Product Area）",
        "Phase 09 总数",
        "可接受关闭",
        "有后续/修复证据",
        "无证据直接关闭",
        "其中 QGate 严重",
        "其中优先复核",
        "无后续证据",
        "空白模板",
        "实质分析文档",
        "风险级别",
        "客户现场风险说明",
    )
    _write_table(
        sheet,
        start_row=1,
        headers=headers,
        rows=_function_risk_rows(tickets),
        widths=(42, 14, 14, 18, 18, 18, 18, 16, 14, 16, 14, 64),
        table_name="FunctionRiskMatrix",
    )
    sheet.freeze_panes = "A2"
    for row_index in range(2, sheet.max_row + 1):
        risk_level = _text(sheet.cell(row_index, 11).value)
        if risk_level == "最高优先":
            sheet.cell(row_index, 11).fill = RED_FILL
        elif risk_level == "高":
            sheet.cell(row_index, 11).fill = AMBER_FILL
        elif risk_level == "中":
            sheet.cell(row_index, 11).fill = BLUE_FILL
        else:
            sheet.cell(row_index, 11).fill = GREEN_FILL


def _write_detail_sheet(
    sheet: Any,
    *,
    tickets: Sequence[Mapping[str, object]],
    table_name: str,
) -> None:
    _write_table(
        sheet,
        start_row=1,
        headers=DETAIL_HEADERS,
        rows=_detail_rows(tickets),
        widths=DETAIL_WIDTHS,
        table_name=table_name,
    )
    sheet.freeze_panes = "A2"
    _color_detail_risk_cells(sheet)


def _write_methodology(sheet: Any) -> None:
    rows = [
        [
            "范围",
            "仅最终项目 tproject=IDCEVO 的当前 Phase 09；包含 China-specific 功能；仅当 Problem Finder Team 包含 CoC 时排除。原始 project 仅用于审计，不决定范围。",
        ],
        [
            "可接受关闭",
            "明确的低风险/测试质量类原因且没有严重影响证据，或 Child (Duplicate) 且显式关联父票已到 Phase 06。",
        ],
        [
            "后续/修复证据",
            "Target 版本或计划窗口、商业解决日期、明确修复计划评论、09 后进入 06、关联父票在 09 后到 Phase 06，或正向 Reprel Change / Shift。它表示有跟进行为，不证明已交付修复。",
        ],
        [
            "空白延期文档",
            "标准 Postpone/Tolerate 富文本模板没有填写实际内容时标为 Template only，不作为分析或计划证据。",
        ],
        [
            "无证据直接关闭",
            "精确历史路径 01 -> 09，且并非可接受关闭、没有后续/修复证据、没有实质延期/容忍评估文档。此类票进入主复核队列。",
        ],
        [
            "QGate 严重",
            "Showstopper_Candidate 或 Showstopper_Confirmed，且 BI/problem severity 以 04、05 或 06 开头。",
        ],
        [
            "客户现场风险边界",
            "本工作簿标示“客户现场风险无法排除”的审计候选，不依据本地快照断言问题已经逃逸到客户现场。需要结合现场版本、复现、售后数据和责任方确认。",
        ],
        [
            "数据来源",
            "本地 database/source/qgate_raw.db 的 octane_defects、octane_defect_history_events 和 comments 快照。",
        ],
        [
            "自动证据分析与原文",
            "明细和复核队列中的“自动证据分析”与“风险等级依据”由本工作簿的结构化字段、关闭历史和评论规则自动生成。08_证据原文按 Ticket ID 保留完整 description、全部 comments 及所有用于关闭判定的历史事件；超过单元格上限的原文按证据类型和分块序号连续保存。",
        ],
    ]
    _write_table(
        sheet,
        start_row=1,
        headers=("判定项目", "规则与边界"),
        rows=rows,
        widths=(30, 140),
        table_name="Phase09Methodology",
    )
    sheet.freeze_panes = "A2"


def write_phase09_idcevo_excel_report(
    *,
    payload: Mapping[str, object],
    output_path: str | Path,
    llm_summaries_path: str | Path | None = None,
) -> Path:
    summary_inputs_by_ticket_id = {
        _text(record.get("ticket_id")): record
        for record in build_phase09_llm_summary_input_records(payload)
        if _text(record.get("ticket_id"))
    }
    tickets = _apply_llm_summaries(
        _tickets(payload),
        _load_llm_summaries(
            llm_summaries_path,
            expected_inputs_by_ticket_id=summary_inputs_by_ticket_id,
        ),
    )
    priority_tickets = [
        ticket
        for ticket in tickets
        if bool(ticket.get("needs_closure_evidence_review"))
        and ticket.get("risk_tier") == "Priority production-exposure review"
    ]
    severe_tickets = [
        ticket
        for ticket in tickets
        if bool(ticket.get("needs_closure_evidence_review"))
        and bool(ticket.get("is_governed_severe"))
    ]
    direct_close_tickets = [
        ticket for ticket in tickets if bool(ticket.get("needs_closure_evidence_review"))
    ]

    workbook = Workbook()
    summary_sheet = workbook.active
    summary_sheet.title = SUMMARY_SHEET
    reasons_sheet = workbook.create_sheet(REASONS_SHEET)
    function_risk_sheet = workbook.create_sheet(FUNCTION_RISK_SHEET)
    priority_sheet = workbook.create_sheet(PRIORITY_SHEET)
    severe_sheet = workbook.create_sheet(SEVERE_SHEET)
    direct_close_sheet = workbook.create_sheet(DIRECT_CLOSE_SHEET)
    detail_sheet = workbook.create_sheet(DETAIL_SHEET)
    methodology_sheet = workbook.create_sheet(METHODOLOGY_SHEET)
    evidence_sheet = workbook.create_sheet(EVIDENCE_SHEET)

    for sheet in workbook.worksheets:
        sheet.sheet_view.showGridLines = False

    _write_summary(summary_sheet, payload)
    _write_reasons(reasons_sheet, payload)
    _write_function_risk(function_risk_sheet, tickets)
    _write_detail_sheet(priority_sheet, tickets=priority_tickets, table_name="PriorityReviewTickets")
    _write_detail_sheet(severe_sheet, tickets=severe_tickets, table_name="GovernedSevereTickets")
    _write_detail_sheet(direct_close_sheet, tickets=direct_close_tickets, table_name="NoEvidenceDirectCloseTickets")
    _write_detail_sheet(detail_sheet, tickets=tickets, table_name="AllPhase09Tickets")
    _write_methodology(methodology_sheet)
    _write_evidence_sheet(evidence_sheet, tickets)

    resolved_output_path = Path(output_path)
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(resolved_output_path)
    workbook.close()
    return resolved_output_path


def generate_phase09_idcevo_excel_report(
    *,
    db_path: str | Path | None = None,
    output_path: str | Path,
    include_china_specific: bool = True,
    llm_summaries_path: str | Path | None = None,
) -> Path:
    payload = build_phase09_idcevo_report_payload(
        db_path=db_path,
        include_china_specific=include_china_specific,
        include_full_evidence=True,
    )
    return write_phase09_idcevo_excel_report(
        payload=payload,
        output_path=output_path,
        llm_summaries_path=llm_summaries_path,
    )