from __future__ import annotations

import argparse
from hashlib import sha256
from html import unescape as html_unescape
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from backend.analytics.phase09_idcevo_report import (
    build_phase09_idcevo_report_payload,
    build_phase06_idcevo_report_payload,
)


INPUT_VERSION = "phase09-llm-summary-v7"
PROBLEM_EVIDENCE_MAX_CHARS = 4_500
COMMENTS_EVIDENCE_MAX_CHARS = 4_500
HISTORY_EVIDENCE_MAX_CHARS = 4_500
COMMENT_ITEM_MAX_CHARS = 1_000
HISTORY_EVENT_VALUE_MAX_CHARS = 800
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
WHITESPACE_PATTERN = re.compile(r"\s+")
HEX_DIAGNOSTIC_PATTERN = re.compile(r"\b0x[0-9a-f]{3,}\b", re.IGNORECASE)
DTC_CODE_PATTERN = re.compile(r"\bDTC\s+\[diagnostic code\]", re.IGNORECASE)
LONG_TECHNICAL_IDENTIFIER_PATTERN = re.compile(r"\b[A-Za-z][A-Za-z0-9_]{20,}\b")
ERROR_DESCRIPTION_END_PATTERN = re.compile(r"\bend\s+of\s+error\s+description\b", re.IGNORECASE)


def _text(value: object, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _clean_text(value: object) -> str:
    without_tags = HTML_TAG_PATTERN.sub(" ", _text(value))
    normalized = WHITESPACE_PATTERN.sub(" ", html_unescape(without_tags)).strip()
    normalized = HEX_DIAGNOSTIC_PATTERN.sub("[diagnostic code]", normalized)
    normalized = DTC_CODE_PATTERN.sub("diagnostic code", normalized)
    return LONG_TECHNICAL_IDENTIFIER_PATTERN.sub("[technical field]", normalized)


def _bounded_text(value: object, maximum: int) -> str:
    text = _clean_text(value)
    if len(text) <= maximum:
        return text
    head_length = int(maximum * 0.75)
    tail_length = maximum - head_length
    return "{} [source truncated for LLM input] {}".format(
        text[:head_length].rstrip(),
        text[-tail_length:].lstrip(),
    )


def _core_problem_text(value: object) -> str:
    text = _clean_text(value)
    match = ERROR_DESCRIPTION_END_PATTERN.search(text)
    return text[: match.start()].strip() if match else text


def _append_with_limit(items: list[str], value: str, maximum: int) -> None:
    if not value:
        return
    current_length = sum(len(item) for item in items) + max(len(items) - 1, 0)
    remaining = maximum - current_length
    if remaining <= 0:
        return
    if len(value) > remaining:
        items.append(value[:remaining].rstrip() + " [truncated]")
        return
    items.append(value)


def _function_name(ticket: Mapping[str, object]) -> str:
    for key in (
        "solution_cluster",
        "defect_category",
        "product_areas",
        "function2modul",
        "assigned_ecu",
    ):
        value = _text(ticket.get(key))
        if value and value != "(blank)":
            return value
    return "Not recorded"


def _comments_evidence(ticket: Mapping[str, object]) -> str:
    raw_records = ticket.get("comment_records")
    if not isinstance(raw_records, list):
        return "No comment text retained."

    entries: list[str] = []
    for raw_record in raw_records[-8:]:
        if not isinstance(raw_record, Mapping):
            continue
        comment_text = _bounded_text(raw_record.get("raw_text"), COMMENT_ITEM_MAX_CHARS)
        if not comment_text:
            continue
        entries.append(
            "[{} | {}] {}".format(
                _text(raw_record.get("timestamp"), "Not recorded"),
                _text(raw_record.get("author"), "Not recorded"),
                comment_text,
            )
        )
    if not entries:
        return "No comment text retained."

    bounded_entries: list[str] = []
    for entry in entries:
        _append_with_limit(bounded_entries, entry, COMMENTS_EVIDENCE_MAX_CHARS)
    return "\n".join(bounded_entries)


def _history_evidence(ticket: Mapping[str, object]) -> str:
    raw_events = ticket.get("audit_history_events")
    if not isinstance(raw_events, list):
        return "No closure history retained."

    entries: list[str] = []
    for raw_event in raw_events:
        if not isinstance(raw_event, Mapping):
            continue
        field_name = _text(raw_event.get("field_name"), "Not recorded")
        old_value = _bounded_text(raw_event.get("old_value"), HISTORY_EVENT_VALUE_MAX_CHARS)
        new_value = _bounded_text(raw_event.get("new_value"), HISTORY_EVENT_VALUE_MAX_CHARS)
        entries.append(
            "[{} | {} | {}] {} -> {}".format(
                _text(raw_event.get("timestamp"), "Not recorded"),
                _text(raw_event.get("actor"), "Not recorded"),
                field_name,
                old_value or "(blank)",
                new_value or "(blank)",
            )
        )
    if not entries:
        return "No closure history retained."

    bounded_entries: list[str] = []
    for entry in entries:
        _append_with_limit(bounded_entries, entry, HISTORY_EVIDENCE_MAX_CHARS)
    return "\n".join(bounded_entries)


def _closure_evidence(ticket: Mapping[str, object]) -> str:
    lifecycle = "Direct 01 -> 09: {}; reopened after 09: {}; entered 06 after 09: {}.".format(
        "yes" if bool(ticket.get("direct_close")) else "no",
        "yes" if bool(ticket.get("reopened_after_phase09")) else "no",
        "yes" if bool(ticket.get("entered_phase06_after_phase09")) else "no",
    )
    return "\n".join(
        (
            lifecycle,
            "Reject/closure reason: {} (source: {}).".format(
                _text(ticket.get("effective_reason"), "Not recorded"),
                _text(ticket.get("reason_source"), "Not recorded"),
            ),
            "Rejection route: {}.".format(
                _text(ticket.get("rejection_route"), "Not recorded")
            ),
            "Follow-up status: {}; target version: {}; planning window: {}; commercial solution date: {}; Reprel/Shift: {}.".format(
                _text(ticket.get("follow_up_status"), "Not recorded"),
                _text(ticket.get("target_version_value"), "Not recorded"),
                _text(ticket.get("planning_window_value"), "Not recorded"),
                _text(ticket.get("commercial_solution_date_value"), "Not recorded"),
                _text(ticket.get("reprel_change_status"), "Not recorded"),
            ),
            "Postponement document: {}; related resolution: {}.".format(
                _text(ticket.get("postponement_document_status"), "Not recorded"),
                _text(ticket.get("related_resolution_status"), "Not recorded"),
            ),
        )
    )


def _risk_evidence(ticket: Mapping[str, object]) -> str:
    risk_flags = ticket.get("risk_flags")
    flag_text = "; ".join(str(flag) for flag in risk_flags) if isinstance(risk_flags, list) else "None"
    return "\n".join(
        (
            "Closure disposition: {}.".format(
                _text(ticket.get("closure_disposition"), "Not recorded")
            ),
            "Risk tier: {}; score: {}.".format(
                _text(ticket.get("risk_tier"), "Not recorded"),
                _text(ticket.get("risk_score"), "Not recorded"),
            ),
            "Severity: {}; QGate classification / BI: {} / {}; governed severe: {}.".format(
                _text(ticket.get("severity"), "Not recorded"),
                _text(ticket.get("reporting_class"), "Not recorded"),
                _text(ticket.get("problem_severity"), "Not recorded"),
                "yes" if bool(ticket.get("is_governed_severe")) else "no",
            ),
            "Production/customer assessment: {}.".format(
                _text(ticket.get("production_exposure_status"), "Not recorded")
            ),
            "Risk flags: {}.".format(flag_text),
        )
    )


def _audit_facts(ticket: Mapping[str, object]) -> dict[str, object]:
    risk_flags = ticket.get("risk_flags")
    return {
        "reject_reason": _text(ticket.get("effective_reason")),
        "reason_source": _text(ticket.get("reason_source")),
        "rejection_route": _text(ticket.get("rejection_route")),
        "direct_close": bool(ticket.get("direct_close")),
        "reopened_after_phase09": bool(ticket.get("reopened_after_phase09")),
        "entered_phase06_after_phase09": bool(ticket.get("entered_phase06_after_phase09")),
        "follow_up_status": _text(ticket.get("follow_up_status")),
        "target_version": _text(ticket.get("target_version_value")),
        "planning_window": _text(ticket.get("planning_window_value")),
        "commercial_solution_date": _text(ticket.get("commercial_solution_date_value")),
        "reprel_change_status": _text(ticket.get("reprel_change_status")),
        "postponement_document_status": _text(ticket.get("postponement_document_status")),
        "related_resolution_status": _text(ticket.get("related_resolution_status")),
        "risk_tier": _text(ticket.get("risk_tier")),
        "risk_score": ticket.get("risk_score"),
        "reporting_class": _text(ticket.get("reporting_class")),
        "problem_severity": _text(ticket.get("problem_severity")),
        "severity": _text(ticket.get("severity")),
        "production_exposure_status": _text(ticket.get("production_exposure_status")),
        "risk_flags": [str(flag) for flag in risk_flags] if isinstance(risk_flags, list) else [],
    }


def _summary_input_ticket(ticket: Mapping[str, object]) -> dict[str, object]:
    return {
        "title": _text(ticket.get("name"), "Not recorded"),
        "function": _function_name(ticket),
        "problem_evidence": _bounded_text(
            _core_problem_text(
                ticket.get("description_full", ticket.get("description_excerpt"))
            ),
            PROBLEM_EVIDENCE_MAX_CHARS,
        )
        or "No ticket description retained.",
        "closure_evidence": _closure_evidence(ticket),
        "risk_evidence": _risk_evidence(ticket),
        "comments_evidence": _comments_evidence(ticket),
        "history_evidence": _history_evidence(ticket),
        "audit_facts": _audit_facts(ticket),
    }


def build_phase09_llm_summary_input_records(
    payload: Mapping[str, object],
    *,
    review_only: bool = True,
    limit: int | None = None,
) -> list[dict[str, object]]:
    raw_tickets = payload.get("tickets")
    if not isinstance(raw_tickets, list):
        return []

    records: list[dict[str, object]] = []
    seen_ticket_ids: set[str] = set()
    for raw_ticket in raw_tickets:
        if not isinstance(raw_ticket, Mapping):
            continue
        if review_only and not bool(raw_ticket.get("needs_closure_evidence_review")):
            continue
        ticket_id = _text(raw_ticket.get("ticket_id"))
        if not ticket_id:
            continue
        if ticket_id in seen_ticket_ids:
            raise ValueError(f"Duplicate Phase 09 LLM summary ticket id: {ticket_id}")
        seen_ticket_ids.add(ticket_id)
        ticket = _summary_input_ticket(raw_ticket)
        fingerprint_payload = {
            "input_version": INPUT_VERSION,
            "ticket_id": ticket_id,
            "ticket": ticket,
        }
        fingerprint = sha256(
            json.dumps(
                fingerprint_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        records.append(
            {
                "ticket_id": ticket_id,
                "input_fingerprint": fingerprint,
                "input_version": INPUT_VERSION,
                "ticket": ticket,
            }
        )
        if limit is not None and len(records) >= limit:
            break
    return records


def write_phase09_llm_summary_input_jsonl(
    records: Sequence[Mapping[str, object]],
    output_path: str | Path,
) -> int:
    resolved_output_path = Path(output_path)
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        for record in records
    )
    resolved_output_path.write_text(content, encoding="utf-8")
    return len(records)


def export_phase09_llm_summary_inputs(
    *,
    db_path: str | Path | None = None,
    output_path: str | Path,
    include_china_specific: bool = True,
    review_only: bool = True,
    limit: int | None = None,
) -> int:
    payload = build_phase09_idcevo_report_payload(
        db_path=db_path,
        include_china_specific=include_china_specific,
        include_full_evidence=True,
    )
    records = build_phase09_llm_summary_input_records(
        payload,
        review_only=review_only,
        limit=limit,
    )
    return write_phase09_llm_summary_input_jsonl(records, output_path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export bounded Phase 09 ticket evidence for LLM summaries."
    )
    parser.add_argument("--output", required=True, help="Output JSONL path")
    parser.add_argument("--db-path", default=None, help="Source SQLite database path")
    parser.add_argument(
        "--all-tickets",
        action="store_true",
        help="Export all scoped Phase 09 tickets instead of only the closure-review queue.",
    )
    parser.add_argument(
        "--exclude-china-specific",
        action="store_true",
        help="Use the legacy Global-only scope instead of all IDCEVO functions.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Maximum ticket records to export")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be positive")
    count = export_phase09_llm_summary_inputs(
        db_path=args.db_path,
        output_path=args.output,
        include_china_specific=not args.exclude_china_specific,
        review_only=not args.all_tickets,
        limit=args.limit,
    )
    print(f"Wrote {count} Phase 09 LLM summary inputs to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# ---------------------------------------------------------------------------
# Phase 06 (resolved / concluded tickets) LLM summary input builder
# ---------------------------------------------------------------------------

PHASE06_INPUT_VERSION = "phase06-llm-summary-v1"


def build_phase06_llm_summary_input_records(
    payload: Mapping[str, object],
    *,
    limit: int | None = None,
) -> list[dict[str, object]]:
    """Build bounded LLM input records for ALL Phase 06 tickets in the payload.

    Phase 06 has no ``needs_closure_evidence_review`` filter — every ticket in
    the payload is exported.
    """
    raw_tickets = payload.get("tickets")
    if not isinstance(raw_tickets, list):
        return []

    records: list[dict[str, object]] = []
    seen_ticket_ids: set[str] = set()
    for raw_ticket in raw_tickets:
        if not isinstance(raw_ticket, Mapping):
            continue
        ticket_id = _text(raw_ticket.get("ticket_id"))
        if not ticket_id:
            continue
        if ticket_id in seen_ticket_ids:
            raise ValueError(f"Duplicate Phase 06 LLM summary ticket id: {ticket_id}")
        seen_ticket_ids.add(ticket_id)
        ticket = _summary_input_ticket(raw_ticket)
        fingerprint_payload = {
            "input_version": PHASE06_INPUT_VERSION,
            "ticket_id": ticket_id,
            "ticket": ticket,
        }
        fingerprint = sha256(
            json.dumps(
                fingerprint_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        records.append(
            {
                "ticket_id": ticket_id,
                "input_fingerprint": fingerprint,
                "input_version": PHASE06_INPUT_VERSION,
                "ticket": ticket,
            }
        )
        if limit is not None and len(records) >= limit:
            break
    return records


def write_phase06_llm_summary_input_jsonl(
    records: Sequence[Mapping[str, object]],
    output_path: str | Path,
) -> int:
    resolved_output_path = Path(output_path)
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        for record in records
    )
    resolved_output_path.write_text(content, encoding="utf-8")
    return len(records)


def export_phase06_llm_summary_inputs(
    *,
    db_path: str | Path | None = None,
    output_path: str | Path,
    include_china_specific: bool = True,
    limit: int | None = None,
) -> int:
    payload = build_phase06_idcevo_report_payload(
        db_path=db_path,
        include_china_specific=include_china_specific,
        include_full_evidence=True,
    )
    records = build_phase06_llm_summary_input_records(
        payload,
        limit=limit,
    )
    return write_phase06_llm_summary_input_jsonl(records, output_path)