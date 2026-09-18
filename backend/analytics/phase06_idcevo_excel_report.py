"""Phase 06 IDCEVO resolution-quality Excel report generator.

Generates a multi-sheet Excel for Phase 06-Concluded (resolved) IDCEVO tickets
with LLM summaries (root-cause / fix-quality / escape-risk) applied.

Reuses most sheet-writing functions from phase09_idcevo_excel_report via import;
only the payload builder, LLM input builder, fallback summary text, and sheet
naming differ.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from openpyxl import Workbook

from backend.analytics.phase09_idcevo_excel_report import (
    _cell_value,
    _detail_rows,
    _set_widths,
    _summary_safe_title,
    _text,
    _tickets,
    _write_detail_sheet,
    _write_evidence_sheet,
    _write_function_risk,
    _write_methodology,
    _write_reasons,
    _write_summary,
)
from backend.analytics.phase09_idcevo_llm_summary import (
    build_phase06_llm_summary_input_records,
)
from backend.analytics.phase09_idcevo_report import build_phase06_idcevo_report_payload

# Phase 06 sheet names
SUMMARY_SHEET = "00_总览"
REASONS_SHEET = "01_修复效率"
FUNCTION_RISK_SHEET = "02_功能风险"
PRIORITY_SHEET = "03_逃逸风险复核"
SEVERE_SHEET = "04_根因质量复核"
DETAIL_SHEET = "05_全部明细"
METHODOLOGY_SHEET = "06_判定口径"
EVIDENCE_SHEET = "07_证据原文"


def _fallback_processing_summary_phase06(ticket: Mapping[str, object]) -> str:
    """Rule-based fallback for Phase 06 tickets without an LLM summary."""
    return "\n".join(
        (
            "根因：{}。".format(_summary_safe_title(ticket.get("name"))),
            "修复质量：直接 01 -> 06：{}；06 后重开：{}。".format(
                "是" if bool(ticket.get("direct_close")) else "否",
                "是" if bool(ticket.get("reopened_after_phase06")) else "否",
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
            "风险判断与逃逸回顾：{}（风险分：{}）。{}。LLM 根因/修复质量/逃逸风险摘要待补全；完整原文见 07_证据原文。".format(
                _text(ticket.get("risk_tier"), "Not recorded"),
                _text(ticket.get("risk_score"), "Not recorded"),
                _text(ticket.get("production_exposure_status"), "Not recorded"),
            ),
        )
    )


PHASE06_REQUIRED_SUMMARY_LABELS = (
    "根因：",
    "修复质量：",
    "Reject/关闭原因：",
    "后续闭环：",
    "风险判断与逃逸回顾：",
)


def _is_valid_phase06_summary(value: object) -> bool:
    summary = _text(value)
    normalized = summary.casefold()
    return (
        bool(summary)
        and all(label in summary for label in PHASE06_REQUIRED_SUMMARY_LABELS)
        and "<html" not in normalized
        and "end of error description" not in normalized
    )


def _load_llm_summaries_phase06(
    path: str | Path | None,
    *,
    expected_inputs_by_ticket_id: Mapping[str, Mapping[str, object]],
) -> dict[str, dict[str, str]]:
    """Load Phase 06 LLM summaries with Phase 06 label validation."""
    if path is None:
        return {}

    summary_path = Path(path)
    if not summary_path.is_file():
        raise FileNotFoundError(f"LLM summary file does not exist: {summary_path}")

    summaries: dict[str, dict[str, str]] = {}
    for line in summary_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw_summary = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw_summary, Mapping):
            continue
        ticket_id = _text(raw_summary.get("ticket_id"))
        summary = _text(raw_summary.get("summary"))
        expected_input = expected_inputs_by_ticket_id.get(ticket_id)
        if (
            not ticket_id
            or not _is_valid_phase06_summary(summary)
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


def _apply_llm_summaries_phase06(
    tickets: Sequence[Mapping[str, object]],
    summaries_by_ticket_id: Mapping[str, Mapping[str, str]],
) -> list[dict[str, object]]:
    """Apply LLM summaries to Phase 06 tickets, with Phase 06 fallback text."""
    enriched_tickets: list[dict[str, object]] = []
    for ticket in tickets:
        enriched_ticket = dict(ticket)
        ticket_id = _text(enriched_ticket.get("ticket_id"))
        summary = summaries_by_ticket_id.get(ticket_id)
        if summary is None:
            enriched_ticket["llm_processing_summary"] = _fallback_processing_summary_phase06(
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


def write_phase06_idcevo_excel_report(
    *,
    payload: Mapping[str, object],
    output_path: str | Path,
    llm_summaries_path: str | Path | None = None,
) -> Path:
    """Build the Phase 06 multi-sheet workbook with LLM summaries applied."""
    # Build expected inputs with Phase 06 fingerprint/version (NOT Phase 09 v7)
    summary_inputs_by_ticket_id = {
        _text(record.get("ticket_id")): record
        for record in build_phase06_llm_summary_input_records(payload)
        if _text(record.get("ticket_id"))
    }
    tickets = _apply_llm_summaries_phase06(
        _tickets(payload),
        _load_llm_summaries_phase06(
            llm_summaries_path,
            expected_inputs_by_ticket_id=summary_inputs_by_ticket_id,
        ),
    )

    # Phase 06 sub-views: escape-risk flagged + root-cause quality concern
    escape_risk_tickets = [
        ticket
        for ticket in tickets
        if "escape" in str(ticket.get("risk_tier", "")).lower()
        or "escape" in str(ticket.get("production_exposure_status", "")).lower()
    ]
    root_cause_concern_tickets = [
        ticket
        for ticket in tickets
        if "root" in str(ticket.get("risk_tier", "")).lower()
        or "concern" in str(ticket.get("risk_tier", "")).lower()
    ]

    workbook = Workbook()
    summary_sheet = workbook.active
    summary_sheet.title = SUMMARY_SHEET
    reasons_sheet = workbook.create_sheet(REASONS_SHEET)
    function_risk_sheet = workbook.create_sheet(FUNCTION_RISK_SHEET)
    priority_sheet = workbook.create_sheet(PRIORITY_SHEET)
    severe_sheet = workbook.create_sheet(SEVERE_SHEET)
    detail_sheet = workbook.create_sheet(DETAIL_SHEET)
    methodology_sheet = workbook.create_sheet(METHODOLOGY_SHEET)
    evidence_sheet = workbook.create_sheet(EVIDENCE_SHEET)

    for sheet in workbook.worksheets:
        sheet.sheet_view.showGridLines = False

    _write_summary(summary_sheet, payload)
    _write_reasons(reasons_sheet, payload)
    _write_function_risk(function_risk_sheet, tickets)
    _write_detail_sheet(priority_sheet, tickets=escape_risk_tickets, table_name="EscapeRiskTickets")
    _write_detail_sheet(severe_sheet, tickets=root_cause_concern_tickets, table_name="RootCauseConcernTickets")
    _write_detail_sheet(detail_sheet, tickets=tickets, table_name="AllPhase06Tickets")
    _write_methodology(methodology_sheet)
    _write_evidence_sheet(evidence_sheet, tickets)

    resolved_output_path = Path(output_path)
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(resolved_output_path)
    workbook.close()
    return resolved_output_path


def generate_phase06_idcevo_excel_report(
    *,
    db_path: str | Path | None = None,
    output_path: str | Path,
    include_china_specific: bool = True,
    llm_summaries_path: str | Path | None = None,
) -> Path:
    """End-to-end: DB → payload → Excel with LLM summaries."""
    payload = build_phase06_idcevo_report_payload(
        db_path=db_path,
        include_china_specific=include_china_specific,
        include_full_evidence=True,
    )
    return write_phase06_idcevo_excel_report(
        payload=payload,
        output_path=output_path,
        llm_summaries_path=llm_summaries_path,
    )
