from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from html import escape as html_escape
from html import unescape as html_unescape
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from backend.analytics.china_scope import is_china_specific
from backend.analytics.full_picture_outcomes import _extract_preferred_phase_code
from backend.analytics.qgate_kpi_report_common import (
    require_tables,
    resolve_report_db_path,
)


NORMAL_CLOSURE_REASONS = frozenset(
    {
        "expected behaviour",
        "expected behavior",
        "not reproducible",
        "user error",
        "measuring tool issue",
        "invalid testcase",
        "insufficient defect quality",
    }
)

DECISION_CLOSURE_REASONS = frozenset(
    {
        "management decision",
        "tolerated",
        "function not implemented/testable yet",
        "cr - conceptional issue",
    }
)

REASON_HISTORY_FIELDS = frozenset(
    {
        "blocking_reason_udf",
        "blocked_reason",
        "rejected_status_udf",
    }
)

TARGET_VERSION_HISTORY_FIELDS = frozenset(
    {
        "exp_sol_version_udf",
        "legacy_target_i_step_udf",
        "req_target_istep_udf",
        "requested_target_i_step_release_udf",
        "target_i_step_udf",
    }
)

PLANNING_WINDOW_HISTORY_FIELDS = frozenset(
    {
        "supplier_confirmed_target_week_udf",
        "target_set_udf",
        "target_week_udf",
    }
)

REPREL_CHANGE_HISTORY_FIELD = "reprel_changes_udf"
POSTPONEMENT_DOCUMENT_HISTORY_FIELD = "postpone_tolerate_doc_udf"
POSTPONEMENT_DECISION_DATE_HISTORY_FIELD = "postpone_toleratedate_udf"
COMMERCIAL_SOLUTION_DATE_HISTORY_FIELD = "com_sol_avail_date_udf"
HISTORY_AUDIT_FIELDS = (
    REASON_HISTORY_FIELDS
    | TARGET_VERSION_HISTORY_FIELDS
    | PLANNING_WINDOW_HISTORY_FIELDS
    | frozenset(
        {
            REPREL_CHANGE_HISTORY_FIELD,
            POSTPONEMENT_DOCUMENT_HISTORY_FIELD,
            POSTPONEMENT_DECISION_DATE_HISTORY_FIELD,
            COMMERCIAL_SOLUTION_DATE_HISTORY_FIELD,
        }
    )
)
NON_TARGET_VERSION_VALUES = frozenset(
    {
        "-",
        ".",
        "n/a",
        "na",
        "no i-step relevance",
        "none",
        "not applicable",
        "not relevant",
    }
)

COC_DEVELOPMENT_TEAM_TOKEN = "coc"
GOVERNED_SEVERE_REPORTING_CLASSES = frozenset(
    {"showstopper_candidate", "showstopper_confirmed"}
)
GOVERNED_SEVERE_BI_PREFIXES = frozenset({"04", "05", "06"})

PLAN_PATTERNS = (
    re.compile(r"\b(?:fix|solution|implementation|change)\s+(?:is\s+)?(?:planned|scheduled)\b", re.IGNORECASE),
    re.compile(r"\bplanned\s+(?:for|in|with)\s+(?:release|build|cw|week|version|fix|implementation)\b", re.IGNORECASE),
    re.compile(r"\b(?:target|targeted)\s+(?:release|build|cw|week|version)\b", re.IGNORECASE),
    re.compile(r"\b(?:will|to be)\s+(?:fixed|resolved|implemented)\b", re.IGNORECASE),
    re.compile(r"\b(?:fixed|resolved)\s+in\s+(?:release|build|version|cw|week)\b", re.IGNORECASE),
)

NO_FIX_PATTERNS = (
    re.compile(r"\b(?:no|not)\s+(?:fix|repair|solution)\b", re.IGNORECASE),
    re.compile(r"\bwill\s+not\s+(?:be\s+)?(?:fixed|implemented)\b", re.IGNORECASE),
    re.compile(r"\bnot\s+planned\s+(?:to\s+)?(?:fix|implement)\b", re.IGNORECASE),
)

MANAGEMENT_DECISION_PATTERN = re.compile(r"\bmanagement\s+decision\b", re.IGNORECASE)
TICKET_REDUCTION_PATTERN = re.compile(r"ticket\s+reduction\s+measure", re.IGNORECASE)
ARCHIVE_NOTE_PATTERN = re.compile(r"under the archiving process", re.IGNORECASE)
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
NUMERIC_VALUE_PATTERN = re.compile(r"^\d+$")
WHITESPACE_PATTERN = re.compile(r"\s+")

POSTPONEMENT_TEMPLATE_LABELS = (
    "event chain-overlapping error description from the coc perspective:",
    "error recovery / frequency prognosis based on development / field data:",
    "customer impact (short / permanent malfunction indication, ccm...):",
    "customer impact (short / permanent malfunction indication,...):",
    "diagnostic impact (expected dtcs):",
    "instructions for plant process (e.g.: false errors causing rework, prolonging the process time, dtc, ba/nael):",
    "impact and instructions for sales organization (extra service instructions necessary, zedis updates, etc.):",
    "impact and instructions for sales organisation (extra service instructions necessary, zedis updates, etc.):",
    "documentation of the decision <date>.:",
    "function owner/coc:",
    "function owner:",
    "plant:",
    "validation intensive testing:",
    "aftersales:",
    "eg-7:",
    "ca-6:",
    "m50 management:",
    "rsu / system function:",
    "e/e mqi from aq-5/6/7:",
    "e/e mqi from aq-5/6/7/9:",
    "e/e quality (aq/zx-q):",
)


def _text(value: object) -> str:
    return str(value or "").strip()


def _normalized(value: object) -> str:
    return _text(value).casefold()


def _is_coc_development_team(value: object) -> bool:
    return COC_DEVELOPMENT_TEAM_TOKEN in _normalized(value)


def _is_coc_development_ticket(
    *,
    problem_finder_team: object,
) -> bool:
    return _is_coc_development_team(problem_finder_team)


def _is_governed_severe(record: dict[str, Any]) -> bool:
    reporting_class = _normalized(record["reporting_class"])
    problem_severity = _normalized(record["problem_severity"])
    return (
        reporting_class in GOVERNED_SEVERE_REPORTING_CLASSES
        and problem_severity[:2] in GOVERNED_SEVERE_BI_PREFIXES
    )


def _is_concrete_target_version(value: object) -> bool:
    normalized = _normalized(value)
    return bool(normalized) and normalized not in NON_TARGET_VERSION_VALUES


def _is_concrete_commercial_solution_date(value: object) -> bool:
    normalized = _normalized(value)
    return bool(normalized) and not normalized.startswith("1970-01-01")


def _parse_reprel_change_count(value: object) -> int | None:
    text = _text(value)
    if not NUMERIC_VALUE_PATTERN.fullmatch(text):
        return None
    return int(text)


def _display(value: object, fallback: str = "Not recorded") -> str:
    text = _text(value)
    return text or fallback


def _year_from_record(year: object, creation_time: object) -> str:
    text = _text(year)
    if text:
        return text
    creation_text = _text(creation_time)
    match = re.search(r"(20\d{2})", creation_text)
    return match.group(1) if match else "Unknown"


def _phase_code(value: object) -> str:
    return _extract_preferred_phase_code(value)


def _phase_is_resolved(value: object) -> bool:
    return _phase_code(value) == "06"


def _clean_comment_text(value: object) -> str:
    without_tags = HTML_TAG_PATTERN.sub(" ", _text(value))
    return WHITESPACE_PATTERN.sub(" ", html_unescape(without_tags)).strip()


def _comment_author(value: object) -> str:
    if isinstance(value, dict):
        return (
            _text(value.get("full_name"))
            or _text(value.get("name"))
            or _text(value.get("id"))
        )
    return _text(value)


def _has_substantive_assessment_document(value: object) -> bool:
    document = _clean_comment_text(value)
    normalized_document = _normalized(document)
    if not normalized_document:
        return False
    if not normalized_document.startswith(
        "event chain-overlapping error description from the coc perspective:"
    ):
        return True
    if "documentation of the decision <date>.:" not in normalized_document:
        return True
    remaining_text = normalized_document
    for label in POSTPONEMENT_TEMPLATE_LABELS:
        remaining_text = remaining_text.replace(label, " ")
    return bool(re.sub(r"[^a-z0-9]+", "", remaining_text))


def _comment_evidence(
    value: object,
    *,
    capture_comment_records: bool = False,
) -> dict[str, object]:
    try:
        parsed = json.loads(_text(value) or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = []

    records: list[tuple[str, str]] = []
    comment_records: list[dict[str, str]] = []
    comment_count = 0
    if isinstance(parsed, list):
        for item in parsed:
            if not isinstance(item, dict):
                continue
            raw_text = _text(item.get("text"))
            text = _clean_comment_text(raw_text)
            if raw_text:
                comment_count += 1
                if capture_comment_records:
                    comment_records.append(
                        {
                            "comment_id": _text(item.get("id")),
                            "timestamp": _text(item.get("creation_time")),
                            "author": _comment_author(item.get("author")),
                            "raw_text": raw_text,
                            "readable_text": text,
                        }
                    )
            if not text or ARCHIVE_NOTE_PATTERN.search(text):
                continue
            records.append((_text(item.get("creation_time")), text))

    plan_records: list[tuple[str, str]] = []
    no_fix_records: list[tuple[str, str]] = []
    decision_records: list[tuple[str, str]] = []
    ticket_reduction_records: list[tuple[str, str]] = []
    for timestamp, text in records:
        if any(pattern.search(text) for pattern in PLAN_PATTERNS):
            plan_records.append((timestamp, text))
        if any(pattern.search(text) for pattern in NO_FIX_PATTERNS):
            no_fix_records.append((timestamp, text))
        if MANAGEMENT_DECISION_PATTERN.search(text):
            decision_records.append((timestamp, text))
        if TICKET_REDUCTION_PATTERN.search(text):
            ticket_reduction_records.append((timestamp, text))

    return {
        "comment_count": comment_count,
        "comment_records": comment_records,
        "plan_records": plan_records,
        "no_fix_records": no_fix_records,
        "decision_records": decision_records,
        "ticket_reduction_records": ticket_reduction_records,
    }


def _is_after_or_at(timestamp: str, reference_timestamp: str) -> bool:
    if not timestamp or not reference_timestamp:
        return False
    return timestamp >= reference_timestamp


def _first_excerpt(records: list[tuple[str, str]], max_length: int = 360) -> str:
    if not records:
        return ""
    timestamp, text = records[-1]
    prefix = f"{timestamp}: " if timestamp else ""
    combined = prefix + text
    return combined if len(combined) <= max_length else combined[: max_length - 3].rstrip() + "..."


def _description_excerpt(value: object, max_length: int = 420) -> str:
    text = WHITESPACE_PATTERN.sub(" ", _text(value))
    return text if len(text) <= max_length else text[: max_length - 3].rstrip() + "..."


def _readable_excerpt(value: object, max_length: int = 1_200) -> str:
    text = _clean_comment_text(value)
    return text if len(text) <= max_length else text[: max_length - 3].rstrip() + "..."


def _is_semantic_reason(value: object) -> bool:
    text = _text(value)
    if not text or NUMERIC_VALUE_PATTERN.fullmatch(text):
        return False
    return not _normalized(text).startswith("09 rejected")


def _extract_relation_target(value: object) -> str:
    text = _text(value)
    if not text:
        return ""
    for candidate in re.split(r"[,;|\s]+", text):
        candidate = candidate.strip()
        if candidate:
            return candidate
    return ""


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {
        _text(row[1])
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }


def _optional_column(columns: set[str], name: str) -> str:
    return name if name in columns else "''"


def _history_rows(
    connection: sqlite3.Connection,
    defect_ids: list[str],
    history_columns: set[str],
) -> list[sqlite3.Row]:
    if not defect_ids:
        return []

    entry_index = _optional_column(history_columns, "entry_index")
    change_index = _optional_column(history_columns, "change_index")
    old_value_text = _optional_column(history_columns, "old_value_text")
    new_value_text = _optional_column(history_columns, "new_value_text")
    user_name = _optional_column(history_columns, "user_name")
    audit_fields = ", ".join(f"'{field_name}'" for field_name in sorted(HISTORY_AUDIT_FIELDS))
    rows: list[sqlite3.Row] = []
    for start_index in range(0, len(defect_ids), 800):
        batch = defect_ids[start_index : start_index + 800]
        placeholders = ", ".join("?" for _ in batch)
        rows.extend(
            connection.execute(
                f"""
                SELECT defect_id, event_timestamp, field_name, old_value, new_value,
                       {old_value_text} AS old_value_text,
                       {new_value_text} AS new_value_text,
                       {user_name} AS user_name,
                       {entry_index} AS entry_index,
                       {change_index} AS change_index
                FROM octane_defect_history_events
                WHERE defect_id IN ({placeholders})
                  AND (
                    lower(coalesce(field_name, '')) LIKE '%phase%'
                                        OR lower(coalesce(field_name, '')) IN ({audit_fields})
                  )
                ORDER BY defect_id, event_timestamp, entry_index, change_index
                """,
                batch,
            ).fetchall()
        )
    return rows


def _parent_rows(
    connection: sqlite3.Connection,
    relation_targets: set[str],
    defect_columns: set[str],
) -> dict[str, dict[str, str]]:
    if not relation_targets:
        return {}

    phase = _optional_column(defect_columns, "phase")
    name = _optional_column(defect_columns, "name")
    parents: dict[str, dict[str, str]] = {}
    target_ids = sorted(relation_targets)
    for start_index in range(0, len(target_ids), 800):
        batch = target_ids[start_index : start_index + 800]
        placeholders = ", ".join("?" for _ in batch)
        for row in connection.execute(
            f"""
            SELECT defect_id, {phase} AS phase, {name} AS name
            FROM octane_defects
            WHERE defect_id IN ({placeholders})
            """,
            batch,
        ):
            parents[_text(row["defect_id"])] = {
                "phase": _text(row["phase"]),
                "name": _text(row["name"]),
            }
    return parents


def _append_history_evidence(
    records: dict[str, dict[str, Any]],
    history_rows: list[sqlite3.Row],
    *,
    capture_audit_history: bool = False,
    target_phase: str = "09",
) -> None:
    target_phase_key = f"seen_phase{target_phase}"
    latest_phase_key = f"latest_phase{target_phase}_at"
    reopened_key = f"reopened_after_phase{target_phase}"
    for row in history_rows:
        defect_id = _text(row["defect_id"])
        record = records.get(defect_id)
        if record is None:
            continue
        field_name = _normalized(row["field_name"])
        timestamp = _text(row["event_timestamp"])
        old_value = _text(row["old_value_text"]) or _text(row["old_value"])
        new_value = _text(row["new_value_text"]) or _text(row["new_value"])
        record["audit_history_event_count"] += 1
        if capture_audit_history:
            record["audit_history_events"].append(
                {
                    "timestamp": timestamp,
                    "actor": _text(row["user_name"]),
                    "field_name": _text(row["field_name"]),
                    "old_value": old_value,
                    "new_value": new_value,
                }
            )
        if "phase" in field_name:
            old_phase = _extract_preferred_phase_code(row["old_value_text"], row["old_value"])
            new_phase = _extract_preferred_phase_code(row["new_value_text"], row["new_value"])
            record["phase_history_count"] += 1
            if old_phase == "01" and new_phase == target_phase:
                record["direct_close"] = True
            if new_phase == target_phase:
                record[target_phase_key] = True
                record[latest_phase_key] = timestamp or record[latest_phase_key]
            elif record.get(target_phase_key) and old_phase == target_phase and new_phase and new_phase != target_phase:
                record[reopened_key] = True
            if target_phase == "09" and record.get("seen_phase09") and new_phase == "06":
                record["entered_phase06_after_phase09"] = True
            continue
        value = new_value
        if not value:
            continue
        if field_name in TARGET_VERSION_HISTORY_FIELDS:
            if _is_concrete_target_version(value):
                record["target_version_events"].append((timestamp, field_name, value))
            continue
        if field_name in PLANNING_WINDOW_HISTORY_FIELDS:
            record["planning_window_events"].append((timestamp, field_name, value))
            continue
        if field_name == REPREL_CHANGE_HISTORY_FIELD:
            record["reprel_history_values"].append((timestamp, value))
            continue
        if field_name == POSTPONEMENT_DOCUMENT_HISTORY_FIELD:
            record["postponement_document_events"].append((timestamp, value))
            continue
        if field_name == POSTPONEMENT_DECISION_DATE_HISTORY_FIELD:
            record["postponement_decision_date_events"].append(
                (timestamp, field_name, value)
            )
            continue
        if field_name == COMMERCIAL_SOLUTION_DATE_HISTORY_FIELD:
            if _is_concrete_commercial_solution_date(value):
                record["commercial_solution_date_events"].append(
                    (timestamp, field_name, value)
                )
            continue
        if field_name not in REASON_HISTORY_FIELDS:
            continue
        if field_name == "rejected_status_udf":
            record["rejection_route"] = value
        elif field_name == "blocking_reason_udf" and _is_semantic_reason(value):
            record["history_blocking_reason"] = value
        elif field_name == "blocked_reason" and _is_semantic_reason(value):
            record["history_blocked_reason"] = value


def _latest_phase_entry_timestamps(
    history_rows: list[sqlite3.Row],
    *,
    phase_code: str,
) -> dict[str, str]:
    timestamps: dict[str, str] = {}
    for row in history_rows:
        if "phase" not in _normalized(row["field_name"]):
            continue
        new_phase = _extract_preferred_phase_code(row["new_value_text"], row["new_value"])
        if new_phase == phase_code:
            timestamps[_text(row["defect_id"])] = _text(row["event_timestamp"])
    return timestamps


def _latest_event(
    events: list[tuple[str, str, str]],
) -> tuple[str, str, str]:
    return events[-1] if events else ("", "", "")


def _assess_release_evidence(record: dict[str, Any]) -> None:
    phase09_at = _text(record["latest_phase09_at"])
    target_version_events = list(record["target_version_events"])
    post_phase09_target_events = [
        event
        for event in target_version_events
        if _is_after_or_at(event[0], phase09_at)
    ]
    target_timestamp, target_field, target_value = _latest_event(
        post_phase09_target_events or target_version_events
    )
    record["target_version_value"] = target_value
    record["target_version_field"] = target_field
    record["target_version_at"] = target_timestamp
    if post_phase09_target_events:
        record["target_version_status"] = "Target version recorded after current Phase 09"
    elif target_version_events:
        record["target_version_status"] = "Target version recorded before current Phase 09"
    else:
        record["target_version_status"] = "No target version recorded"

    planning_window_events = list(record["planning_window_events"])
    post_phase09_window_events = [
        event
        for event in planning_window_events
        if _is_after_or_at(event[0], phase09_at)
    ]
    window_timestamp, window_field, window_value = _latest_event(
        post_phase09_window_events or planning_window_events
    )
    record["planning_window_value"] = window_value
    record["planning_window_field"] = window_field
    record["planning_window_at"] = window_timestamp
    if post_phase09_window_events:
        record["planning_window_status"] = "Planning window recorded after current Phase 09"
    elif planning_window_events:
        record["planning_window_status"] = "Planning window recorded before current Phase 09"
    else:
        record["planning_window_status"] = "No planning window recorded"

    reprel_count = _parse_reprel_change_count(record["reprel_changes"])
    reprel_source = "Current Reprel Changes"
    if reprel_count is None:
        for _timestamp, raw_value in reversed(record["reprel_history_values"]):
            reprel_count = _parse_reprel_change_count(raw_value)
            if reprel_count is not None:
                reprel_source = "Reprel Changes history"
                break
    record["reprel_change_count"] = reprel_count
    record["reprel_change_source"] = reprel_source if reprel_count is not None else ""
    if reprel_count is None:
        record["reprel_change_status"] = "Reprel change count not recorded"
    elif reprel_count == 0:
        record["reprel_change_status"] = "No recorded Reprel changes: 0"
    else:
        record["reprel_change_status"] = f"Recorded Reprel changes: {reprel_count}"

    commercial_solution_date_events = list(record["commercial_solution_date_events"])
    post_phase09_commercial_solution_date_events = [
        event
        for event in commercial_solution_date_events
        if _is_after_or_at(event[0], phase09_at)
    ]
    commercial_timestamp, commercial_field, commercial_value = _latest_event(
        post_phase09_commercial_solution_date_events or commercial_solution_date_events
    )
    record["commercial_solution_date_value"] = commercial_value
    record["commercial_solution_date_field"] = commercial_field
    record["commercial_solution_date_at"] = commercial_timestamp
    if post_phase09_commercial_solution_date_events:
        record["commercial_solution_date_status"] = (
            "Commercial solution date recorded after current Phase 09"
        )
    elif commercial_solution_date_events:
        record["commercial_solution_date_status"] = (
            "Commercial solution date recorded before current Phase 09"
        )
    else:
        record["commercial_solution_date_status"] = "No commercial solution date recorded"

    postponement_decision_date_events = list(record["postponement_decision_date_events"])
    postponement_timestamp, postponement_field, postponement_value = _latest_event(
        postponement_decision_date_events
    )
    record["postponement_decision_date_value"] = postponement_value
    record["postponement_decision_date_field"] = postponement_field
    record["postponement_decision_date_at"] = postponement_timestamp
    record["postponement_decision_date_status"] = (
        "Postponement/tolerance decision date recorded"
        if postponement_decision_date_events
        else "No postponement/tolerance decision date recorded"
    )


def _assess_postponement_document(record: dict[str, Any]) -> None:
    document_events = list(record["postponement_document_events"])
    if not document_events:
        record["postponement_document_at"] = ""
        record["postponement_document_excerpt"] = ""
        record["postponement_document_status"] = "Not recorded"
        record["has_substantive_assessment_document"] = False
        return

    timestamp, document = document_events[-1]
    record["postponement_document_at"] = timestamp
    record["postponement_document_excerpt"] = _description_excerpt(document)
    if _has_substantive_assessment_document(document):
        record["postponement_document_status"] = "Substantive assessment recorded"
        record["has_substantive_assessment_document"] = True
    else:
        record["postponement_document_status"] = "Template only"
        record["has_substantive_assessment_document"] = False


def _assess_related_resolution(
    record: dict[str, Any],
    parents: dict[str, dict[str, str]],
) -> None:
    relation_target = _extract_relation_target(record["relation_to"])
    parent = parents.get(relation_target, {})
    parent_phase = _text(parent.get("phase"))
    parent_phase06_at = _text(parent.get("phase06_at"))
    phase09_at = _text(record["latest_phase09_at"])
    record["duplicate_parent_id"] = relation_target
    record["duplicate_parent_phase"] = parent_phase
    record["duplicate_parent_name"] = _text(parent.get("name"))
    record["related_resolution_ticket_id"] = relation_target
    record["related_resolution_phase06_at"] = parent_phase06_at

    if not relation_target:
        related_resolution_status = "No explicit linked ticket"
    elif not parent:
        related_resolution_status = "Explicit linked ticket unavailable in source"
    elif not _phase_is_resolved(parent_phase):
        related_resolution_status = "Explicit linked ticket is not currently Phase 06"
    elif phase09_at and parent_phase06_at and parent_phase06_at >= phase09_at:
        related_resolution_status = "Explicit linked ticket reached Phase 06 after current Phase 09"
    elif parent_phase06_at:
        related_resolution_status = "Explicit linked ticket reached Phase 06 before current Phase 09"
    else:
        related_resolution_status = "Explicit linked ticket is Phase 06; timestamp unavailable"
    record["related_resolution_status"] = related_resolution_status
    record["explicit_linked_resolution_after_phase09"] = (
        related_resolution_status == "Explicit linked ticket reached Phase 06 after current Phase 09"
    )


def _reason_details(record: dict[str, Any]) -> tuple[str, str]:
    current_reason = _text(record["blocking_reason"])
    if current_reason:
        return current_reason, "Current blocking reason"
    history_reason = _text(record["history_blocking_reason"])
    if history_reason:
        return history_reason, "Phase-history blocking reason"
    blocked_reason = _text(record["history_blocked_reason"])
    if blocked_reason:
        return blocked_reason, "Phase-history blocked reason"
    comment_evidence = record["comment_evidence"]
    if comment_evidence["decision_records"] or comment_evidence["ticket_reduction_records"]:
        return "Management decision (comment evidence)", "Comment evidence"
    return "Not recorded", "No structured or comment reason"


def _blocking_reason_group(reason: object) -> str:
    normalized_reason = _normalized(reason)
    if normalized_reason == "child (duplicate)":
        return "Duplicate / linked ticket"
    if normalized_reason in {
        "expected behaviour",
        "expected behavior",
        "user error",
        "invalid testcase",
        "measuring tool issue",
        "insufficient defect quality",
    }:
        return "Expected behaviour / test-quality"
    if normalized_reason in {
        "not reproducible",
        "additional information necessary",
        "further traces necessary",
    }:
        return "Not reproducible / insufficient evidence"
    if normalized_reason in DECISION_CLOSURE_REASONS or "management decision" in normalized_reason:
        return "Decision / tolerance / not implemented"
    if normalized_reason in {
        "pqm transfer",
        "defect handling in supplier system",
        "not responsible",
    }:
        return "External handling / ownership"
    if normalized_reason == "not recorded":
        return "No closure reason recorded"
    return "Other closure reason"


def _severity_points(record: dict[str, Any], flags: list[str]) -> int:
    score = 0
    if _is_governed_severe(record):
        score += 5
        flags.append(
            "Governed severe: {} / {}".format(
                _display(record["reporting_class"]),
                _display(record["problem_severity"]),
            )
        )

    severity = _normalized(record["severity"])
    if severity in {"critical", "very high"}:
        score += 5
        flags.append(f"{_display(record['severity'])} severity")
    elif severity == "high":
        score += 3
        flags.append("High severity")

    problem_severity = _normalized(record["problem_severity"])
    if "safety" in problem_severity or "breakdown" in problem_severity:
        score += 5
        flags.append(_display(record["problem_severity"]))
    elif "customer" in problem_severity or "permanent" in problem_severity:
        score += 3
        flags.append(_display(record["problem_severity"]))

    reporting_class = _normalized(record["reporting_class"])
    if reporting_class in {"showstopper_confirmed", "top_issue_confirmed"}:
        score += 5
        flags.append(_display(record["reporting_class"]))
    elif "showstopper_candidate" in reporting_class or "homologation" in reporting_class:
        score += 2
        flags.append(_display(record["reporting_class"]))
    elif "customer function" in reporting_class or "escalation" in reporting_class:
        score += 2
        flags.append(_display(record["reporting_class"]))
    return score


def _closure_disposition_explanation(record: dict[str, Any]) -> str:
    disposition = _text(record["closure_disposition"])
    if disposition == "Acceptable closure evidence":
        return (
            "有效关闭原因属于低风险/测试质量类，或显式关联父票已经达到 Phase 06；"
            "当前快照未同时出现提升风险的严重度、重开或无修复证据。"
        )
    if disposition == "Follow-up / remediation recorded":
        return (
            "发现了版本、计划窗口、商业解决日期、修复计划、关联解决或 Reprel Shift 中至少一项后续证据；"
            "这说明问题仍被跟进，但不证明修复已经在客户版本中交付。"
        )
    if disposition == "Direct closure without auditable rationale or follow-up":
        return (
            "历史显示直接 01 -> 09，且没有可接受的关闭理由、后续修复/计划证据或实质延期评估文档；"
            "因此需要人工核实关闭理由、现场版本和责任方结论。"
        )
    if disposition == "Documented risk acceptance without delivery evidence":
        return (
            "存在实质的延期/容忍分析或风险接受记录，但没有可审计的版本、修复或交付证据；"
            "应按已接受风险而非已解决问题处理。"
        )
    if disposition == "Decision / tolerance without delivery evidence":
        return (
            "关闭依据是管理决策、容忍或未实现，但未找到可审计的修复、版本或交付证据。"
        )
    return "现有结构化字段、评论和关闭历史不足以形成可接受关闭或后续处置证据。"


def _risk_basis(record: dict[str, Any]) -> str:
    qgate_summary = "{} / {}".format(
        _display(record["reporting_class"]),
        _display(record["problem_severity"]),
    )
    target_detail = _text(record["target_version_value"]) or "Not recorded"
    planning_detail = _text(record["planning_window_value"]) or "Not recorded"
    commercial_detail = _text(record["commercial_solution_date_value"]) or "Not recorded"
    risk_flags = record["risk_flags"]
    flag_text = "; ".join(str(flag) for flag in risk_flags) if risk_flags else "None"
    return "\n".join(
        (
            "风险层级：{}；风险分：{}。".format(
                _display(record["risk_tier"]), record["risk_score"]
            ),
            "QGate 分类 / BI：{}；QGate 严重：{}。".format(
                qgate_summary,
                "是" if record["is_governed_severe"] else "否",
            ),
            "关闭处置：{}。".format(_display(record["closure_disposition"])),
            "关闭理由：{}（{}）。".format(
                _display(record["effective_reason"]), _display(record["reason_source"])
            ),
            "后续跟进：{}；Target：{}；计划窗口：{}；商业解决日期：{}；{}。".format(
                _display(record["follow_up_status"]),
                target_detail,
                planning_detail,
                commercial_detail,
                _display(record["reprel_change_status"]),
            ),
            "延期/容忍文档：{}；关联解决：{}。".format(
                _display(record["postponement_document_status"]),
                _display(record["related_resolution_status"]),
            ),
            "风险标记：{}。".format(flag_text),
            "客户现场判断：{}。".format(
                _display(record["production_exposure_status"])
            ),
        )
    )


def _automated_evidence_analysis(record: dict[str, Any]) -> str:
    description = _readable_excerpt(record["description"])
    issue_summary = description or "源 ticket 未记录问题描述。"
    lifecycle = (
        "历史存在直接生命周期 01 -> 09。"
        if record["direct_close"]
        else "历史未发现直接生命周期 01 -> 09。"
    )
    if record["reopened_after_phase09"]:
        lifecycle += " 此前 Phase 09 后曾重开。"
    if record["entered_phase06_after_phase09"]:
        lifecycle += " 当前 Phase 09 后曾进入 Phase 06。"
    rejection_route = _text(record["rejection_route"]) or "Not recorded"
    comment_evidence = record["comment_evidence"]
    comment_summary = "评论 {} 条".format(comment_evidence["comment_count"])
    if record["plan_evidence_excerpt"]:
        comment_summary += "；发现修复计划评论摘录：{}".format(
            record["plan_evidence_excerpt"]
        )
    if record["decision_evidence_excerpt"]:
        comment_summary += "；发现决策评论摘录：{}".format(
            record["decision_evidence_excerpt"]
        )
    if record["no_fix_evidence_excerpt"]:
        comment_summary += "；发现无修复评论摘录：{}".format(
            record["no_fix_evidence_excerpt"]
        )
    return "\n".join(
        (
            "问题概述：{}。{}".format(_display(record["name"]), issue_summary),
            "关闭事实：当前为 Phase 09；{} 有效关闭理由为 {}（{}）；拒绝路径为 {}。".format(
                lifecycle,
                _display(record["effective_reason"]),
                _display(record["reason_source"]),
                rejection_route,
            ),
            "处置与关联：{} Target 版本：{}；计划窗口：{}；商业解决日期：{}；Reprel/Shift：{}；延期文档：{}；关联票：{}。".format(
                _display(record["follow_up_status"]),
                _text(record["target_version_value"]) or "Not recorded",
                _text(record["planning_window_value"]) or "Not recorded",
                _text(record["commercial_solution_date_value"]) or "Not recorded",
                _display(record["reprel_change_status"]),
                _display(record["postponement_document_status"]),
                _display(record["related_resolution_status"]),
            ),
            "评论与分析证据：{}。".format(comment_summary),
            "自动结论：{} {}".format(
                _closure_disposition_explanation(record),
                _display(record["production_exposure_status"]),
            ),
        )
    )


def _evidence_index(record: dict[str, Any]) -> str:
    description_count = 1 if _text(record["description"]) else 0
    return (
        "08_证据原文：问题描述 {} 条；评论 {} 条；关闭/生命周期历史 {} 条。"
        "按 Ticket ID、证据类型和分块序号重组全文。"
    ).format(
        description_count,
        record["comment_evidence"]["comment_count"],
        record["audit_history_event_count"],
    )


def _assess_record(record: dict[str, Any], parents: dict[str, dict[str, str]]) -> None:
    effective_reason, reason_source = _reason_details(record)
    record["effective_reason"] = effective_reason
    record["reason_source"] = reason_source
    record["blocking_reason_group"] = _blocking_reason_group(effective_reason)
    _assess_related_resolution(record, parents)
    _assess_postponement_document(record)
    has_resolved_duplicate_parent = (
        _normalized(effective_reason) == "child (duplicate)"
        and _phase_is_resolved(record["duplicate_parent_phase"])
    )
    record["has_resolved_duplicate_parent"] = has_resolved_duplicate_parent

    _assess_release_evidence(record)

    comment_evidence = record["comment_evidence"]
    latest_phase09_at = _text(record["latest_phase09_at"])
    post_closure_plan_records = [
        item
        for item in comment_evidence["plan_records"]
        if _is_after_or_at(item[0], latest_phase09_at)
    ]
    record["plan_evidence_excerpt"] = _first_excerpt(post_closure_plan_records or comment_evidence["plan_records"])
    record["decision_evidence_excerpt"] = _first_excerpt(
        comment_evidence["decision_records"] or comment_evidence["ticket_reduction_records"]
    )
    record["no_fix_evidence_excerpt"] = _first_excerpt(comment_evidence["no_fix_records"])

    if record["entered_phase06_after_phase09"]:
        repair_plan_status = "Phase 06 reached after an earlier Phase 09 closure"
    elif post_closure_plan_records:
        repair_plan_status = "Explicit post-closure repair plan"
    elif comment_evidence["plan_records"]:
        repair_plan_status = "Historical repair-plan reference"
    elif has_resolved_duplicate_parent:
        repair_plan_status = "Linked duplicate parent is Phase 06"
    else:
        repair_plan_status = "No repair-plan evidence"
    record["repair_plan_status"] = repair_plan_status
    has_reprel_shift_evidence = (
        isinstance(record["reprel_change_count"], int)
        and int(record["reprel_change_count"]) > 0
    )
    has_target_or_planning_evidence = (
        record["target_version_status"] != "No target version recorded"
        or record["planning_window_status"] != "No planning window recorded"
    )
    has_commercial_solution_evidence = (
        record["commercial_solution_date_status"]
        != "No commercial solution date recorded"
    )
    has_repair_plan_evidence = repair_plan_status != "No repair-plan evidence"
    has_related_resolution_evidence = record["explicit_linked_resolution_after_phase09"]
    has_follow_up_evidence = (
        has_target_or_planning_evidence
        or has_commercial_solution_evidence
        or has_repair_plan_evidence
        or has_related_resolution_evidence
        or has_reprel_shift_evidence
    )
    record["has_follow_up_evidence"] = has_follow_up_evidence
    if record["entered_phase06_after_phase09"] or has_related_resolution_evidence:
        record["follow_up_status"] = "Resolution evidence recorded"
    elif has_target_or_planning_evidence:
        record["follow_up_status"] = "Target version or planning window recorded"
    elif has_commercial_solution_evidence:
        record["follow_up_status"] = "Commercial solution date recorded"
    elif has_repair_plan_evidence:
        record["follow_up_status"] = "Repair plan reference recorded"
    elif has_reprel_shift_evidence:
        record["follow_up_status"] = "Reprel change / shift recorded"
    else:
        record["follow_up_status"] = "No remediation or follow-up evidence"

    flags: list[str] = []
    record["is_governed_severe"] = _is_governed_severe(record)
    score = _severity_points(record, flags)
    serious_impact = score >= 3
    reason_normalized = _normalized(effective_reason)
    decision_closure = (
        reason_normalized in DECISION_CLOSURE_REASONS
        or "management decision" in reason_normalized
    )
    record["decision_closure"] = decision_closure
    if record["reopened_after_phase09"]:
        score += 3
        flags.append("Reopened after an earlier Phase 09 closure")
    if comment_evidence["no_fix_records"]:
        score += 3
        flags.append("Comment states no fix or no repair")
    if decision_closure and repair_plan_status == "No repair-plan evidence":
        score += 2
        flags.append("Decision/tolerance closure without repair evidence")
    if effective_reason == "Not recorded":
        score += 1
        flags.append("No structured closure reason")
    if comment_evidence["ticket_reduction_records"] and repair_plan_status == "No repair-plan evidence":
        score += 1
        flags.append("Generic ticket-reduction decision wording")
    if reason_normalized == "child (duplicate)" and not has_resolved_duplicate_parent:
        score += 1
        flags.append("Linked duplicate parent is unavailable or not resolved")

    normal_reason = reason_normalized in NORMAL_CLOSURE_REASONS
    normal_closure = (
        (normal_reason or has_resolved_duplicate_parent)
        and not serious_impact
        and not record["reopened_after_phase09"]
        and not comment_evidence["no_fix_records"]
    )
    record["decision_closure_without_follow_up"] = (
        decision_closure and not record["has_follow_up_evidence"]
    )
    record["needs_closure_evidence_review"] = (
        record["direct_close"]
        and not normal_closure
        and not record["has_follow_up_evidence"]
        and not record["has_substantive_assessment_document"]
    )
    if record["needs_closure_evidence_review"]:
        flags.append("Direct closure without auditable rationale or follow-up")
    decision_evidence_conflict = (
        normal_reason
        and serious_impact
        and repair_plan_status == "No repair-plan evidence"
    )
    record["decision_evidence_conflict"] = decision_evidence_conflict

    if decision_evidence_conflict:
        risk_tier = "Decision-evidence conflict"
    elif score >= 7 and repair_plan_status not in {
        "Phase 06 reached after an earlier Phase 09 closure",
        "Linked duplicate parent is Phase 06",
    }:
        risk_tier = "Priority production-exposure review"
    elif normal_closure:
        risk_tier = "Normal closure evidence"
    elif repair_plan_status != "No repair-plan evidence":
        risk_tier = "Repair plan / resolution evidence"
    else:
        risk_tier = "Evidence incomplete / monitor"

    if record["direct_close"]:
        flags.append("Direct lifecycle path: 01 -> 09")
    has_remediation_or_resolution_evidence = has_follow_up_evidence
    record["has_remediation_or_resolution_evidence"] = has_remediation_or_resolution_evidence
    if not has_remediation_or_resolution_evidence and risk_tier in {
        "Priority production-exposure review",
        "Decision-evidence conflict",
    }:
        production_exposure_status = "Production exposure cannot be ruled out - priority review"
    elif not has_remediation_or_resolution_evidence and (
        decision_closure or record["needs_closure_evidence_review"]
    ):
        production_exposure_status = "Production exposure cannot be ruled out - closure evidence gap"
    elif not has_remediation_or_resolution_evidence:
        production_exposure_status = "No remediation or linked-resolution evidence - impact not elevated"
    else:
        production_exposure_status = "Remediation or linked-resolution evidence recorded"
    record["risk_score"] = score
    record["risk_tier"] = risk_tier
    record["risk_flags"] = flags
    record["production_exposure_status"] = production_exposure_status
    if normal_closure:
        closure_disposition = "Acceptable closure evidence"
    elif record["has_follow_up_evidence"]:
        closure_disposition = "Follow-up / remediation recorded"
    elif record["needs_closure_evidence_review"]:
        closure_disposition = "Direct closure without auditable rationale or follow-up"
    elif decision_closure and record["has_substantive_assessment_document"]:
        closure_disposition = "Documented risk acceptance without delivery evidence"
    elif decision_closure:
        closure_disposition = "Decision / tolerance without delivery evidence"
    else:
        closure_disposition = "Closure evidence incomplete"
    record["closure_disposition"] = closure_disposition
    record["description_excerpt"] = _description_excerpt(record["description"])
    record["risk_basis"] = _risk_basis(record)
    record["automated_analysis"] = _automated_evidence_analysis(record)
    record["evidence_index"] = _evidence_index(record)


def _counter_rows(records: list[dict[str, Any]], key: str, limit: int = 30) -> list[dict[str, object]]:
    counts = Counter(_display(record.get(key), "(blank)") for record in records)
    return [
        {"label": label, "count": count}
        for label, count in counts.most_common(limit)
    ]


def _risk_tier_rows(records: list[dict[str, Any]]) -> list[dict[str, object]]:
    order = (
        "Priority production-exposure review",
        "Decision-evidence conflict",
        "Evidence incomplete / monitor",
        "Repair plan / resolution evidence",
        "Normal closure evidence",
    )
    counts = Counter(record["risk_tier"] for record in records)
    return [{"label": label, "count": counts.get(label, 0)} for label in order]


def _public_record(
    record: dict[str, Any],
    *,
    include_full_evidence: bool = False,
) -> dict[str, object]:
    public_record: dict[str, object] = {
        "ticket_id": record["ticket_id"],
        "name": record["name"],
        "year": record["year"],
        "creation_time": record["creation_time"],
        "last_modified": record["last_modified"],
        "team": record["team"],
        "solution_cluster": _display(record["solution_cluster"], "(blank)"),
        "defect_category": _display(record["defect_category"], "(blank)"),
        "product_areas": _display(record["product_areas"], "(blank)"),
        "function2modul": _display(record["function2modul"], "(blank)"),
        "function_responsible": _display(record["function_responsible"], "(blank)"),
        "assigned_ecu": _display(record["assigned_ecu"], "(blank)"),
        "ecu_to_modul": _display(record["ecu_to_modul"], "(blank)"),
        "legacy_project": _display(record["legacy_project"], "(blank)"),
        "final_project": _display(record["final_project"], "(blank)"),
        "software_version": _display(record["software_version"], "(blank)"),
        "parent_child_type": _display(record["parent_child_type"], "(blank)"),
        "relation_to": _display(record["relation_to"], "(blank)"),
        "severity": _display(record["severity"], "(blank)"),
        "problem_severity": _display(record["problem_severity"], "(blank)"),
        "reporting_class": _display(record["reporting_class"], "(blank)"),
        "is_governed_severe": bool(record["is_governed_severe"]),
        "effective_reason": record["effective_reason"],
        "reason_source": record["reason_source"],
        "blocking_reason_group": record["blocking_reason_group"],
        "rejection_route": _display(record["rejection_route"], "(blank)"),
        "direct_close": bool(record["direct_close"]),
        "latest_phase09_at": record["latest_phase09_at"],
        "reopened_after_phase09": bool(record["reopened_after_phase09"]),
        "entered_phase06_after_phase09": bool(record["entered_phase06_after_phase09"]),
        "repair_plan_status": record["repair_plan_status"],
        "reprel_changes": _display(record["reprel_changes"], "(blank)"),
        "target_version_status": record["target_version_status"],
        "target_version_value": record["target_version_value"],
        "target_version_field": record["target_version_field"],
        "target_version_at": record["target_version_at"],
        "planning_window_status": record["planning_window_status"],
        "planning_window_value": record["planning_window_value"],
        "planning_window_field": record["planning_window_field"],
        "planning_window_at": record["planning_window_at"],
        "reprel_change_count": record["reprel_change_count"],
        "reprel_change_source": record["reprel_change_source"],
        "reprel_change_status": record["reprel_change_status"],
        "commercial_solution_date_status": record["commercial_solution_date_status"],
        "commercial_solution_date_value": record["commercial_solution_date_value"],
        "commercial_solution_date_field": record["commercial_solution_date_field"],
        "commercial_solution_date_at": record["commercial_solution_date_at"],
        "postponement_decision_date_status": record["postponement_decision_date_status"],
        "postponement_decision_date_value": record["postponement_decision_date_value"],
        "postponement_decision_date_field": record["postponement_decision_date_field"],
        "postponement_decision_date_at": record["postponement_decision_date_at"],
        "follow_up_status": record["follow_up_status"],
        "has_follow_up_evidence": bool(record["has_follow_up_evidence"]),
        "decision_closure_without_follow_up": bool(
            record["decision_closure_without_follow_up"]
        ),
        "needs_closure_evidence_review": bool(
            record["needs_closure_evidence_review"]
        ),
        "closure_disposition": record["closure_disposition"],
        "postponement_document_status": record["postponement_document_status"],
        "postponement_document_at": record["postponement_document_at"],
        "postponement_document_excerpt": record["postponement_document_excerpt"],
        "has_substantive_assessment_document": bool(
            record["has_substantive_assessment_document"]
        ),
        "duplicate_parent_id": record["duplicate_parent_id"],
        "duplicate_parent_phase": record["duplicate_parent_phase"],
        "duplicate_parent_name": record["duplicate_parent_name"],
        "related_resolution_status": record["related_resolution_status"],
        "related_resolution_ticket_id": record["related_resolution_ticket_id"],
        "related_resolution_phase06_at": record["related_resolution_phase06_at"],
        "explicit_linked_resolution_after_phase09": bool(record["explicit_linked_resolution_after_phase09"]),
        "production_exposure_status": record["production_exposure_status"],
        "risk_score": record["risk_score"],
        "risk_tier": record["risk_tier"],
        "risk_flags": record["risk_flags"],
        "risk_basis": record["risk_basis"],
        "automated_analysis": record["automated_analysis"],
        "evidence_index": record["evidence_index"],
        "comment_count": record["comment_evidence"]["comment_count"],
        "audit_history_event_count": record["audit_history_event_count"],
        "description_excerpt": record["description_excerpt"],
        "plan_evidence_excerpt": record["plan_evidence_excerpt"],
        "decision_evidence_excerpt": record["decision_evidence_excerpt"],
        "no_fix_evidence_excerpt": record["no_fix_evidence_excerpt"],
    }
    if include_full_evidence:
        public_record["description_full"] = record["description"]
        public_record["comment_records"] = [
            dict(item) for item in record["comment_evidence"]["comment_records"]
        ]
        public_record["audit_history_events"] = [
            dict(item) for item in record["audit_history_events"]
        ]
    return public_record


def build_phase09_idcevo_report_payload(
    *,
    db_path: str | Path | None = None,
    include_china_specific: bool = False,
    include_full_evidence: bool = False,
) -> dict[str, object]:
    resolved_db_path = resolve_report_db_path(db_path)
    require_tables(resolved_db_path, ("octane_defects", "octane_defect_history_events"))
    population_start = "2025-01-01"
    population_end_exclusive = (
        datetime.now(timezone.utc).date() + timedelta(days=1)
    ).isoformat()

    connection = sqlite3.connect(str(resolved_db_path))
    connection.row_factory = sqlite3.Row
    try:
        defect_columns = _table_columns(connection, "octane_defects")
        history_columns = _table_columns(connection, "octane_defect_history_events")
        required_phase = "phase" in defect_columns
        project_scope_field = "tproject" if "tproject" in defect_columns else "project"
        required_project = project_scope_field in defect_columns
        if not required_phase or not required_project:
            raise ValueError("octane_defects must contain phase and tproject or project columns")

        optional_columns = {
            name: _optional_column(defect_columns, name)
            for name in (
                "name",
                "description",
                "comments",
                "creation_time",
                "last_modified",
                "year",
                "project",
                "tproject",
                "team",
                "problem_finder_team",
                "solution_cluster",
                "defect_category",
                "product_areas",
                "function2modul",
                "function_responsible",
                "assigned_ecu",
                "ecu_to_modul",
                "software_version",
                "blocking_reason",
                "reprel_changes",
                "severity",
                "problem_severity",
                "reporting_class",
                "parent_child_type",
                "relation_to",
                "fetched_at",
            )
        }
        defect_rows = connection.execute(
            """
            SELECT defect_id,
                     phase AS phase,
                   {name} AS name,
                   {description} AS description,
                   {comments} AS comments,
                   {creation_time} AS creation_time,
                   {last_modified} AS last_modified,
                   {year} AS year,
                   {project} AS legacy_project,
                   {tproject} AS final_project,
                   {team} AS team,
                   {problem_finder_team} AS problem_finder_team,
                   {solution_cluster} AS solution_cluster,
                   {defect_category} AS defect_category,
                   {product_areas} AS product_areas,
                   {function2modul} AS function2modul,
                   {function_responsible} AS function_responsible,
                   {assigned_ecu} AS assigned_ecu,
                   {ecu_to_modul} AS ecu_to_modul,
                   {software_version} AS software_version,
                   {blocking_reason} AS blocking_reason,
                   {reprel_changes} AS reprel_changes,
                   {severity} AS severity,
                   {problem_severity} AS problem_severity,
                   {reporting_class} AS reporting_class,
                   {parent_child_type} AS parent_child_type,
                   {relation_to} AS relation_to,
                   {fetched_at} AS fetched_at
            FROM octane_defects
            WHERE upper(trim(coalesce({project_scope}, ''))) = 'IDCEVO'
                            AND creation_time >= ?
                            AND creation_time < ?
            ORDER BY defect_id
                        """.format(
                            **optional_columns,
                            project_scope=_optional_column(
                                defect_columns, project_scope_field
                            ),
                        ),
                        (population_start, population_end_exclusive),
        ).fetchall()

        scoped_records: dict[str, dict[str, Any]] = {}
        idcevo_all_ticket_total = 0
        idcevo_phase09_total = 0
        china_excluded_total = 0
        china_specific_in_scope_total = 0
        coc_development_team_excluded_total = 0
        latest_fetched_at = ""
        latest_modified_at = ""
        for row in defect_rows:
            ticket_id = _text(row["defect_id"])
            if not ticket_id:
                continue
            idcevo_all_ticket_total += 1
            if _phase_code(row["phase"]) != "09":
                continue
            idcevo_phase09_total += 1
            solution_cluster = _text(row["solution_cluster"])
            defect_category = _text(row["defect_category"])
            china_specific_ticket = is_china_specific(
                solution_cluster=solution_cluster,
                defect_category=defect_category,
            )
            if china_specific_ticket:
                if not include_china_specific:
                    china_excluded_total += 1
                    continue
            problem_finder_team = _text(row["problem_finder_team"])
            source_team = _text(row["team"])
            report_team = problem_finder_team or source_team
            if _is_coc_development_ticket(
                problem_finder_team=problem_finder_team,
            ):
                coc_development_team_excluded_total += 1
                continue
            if china_specific_ticket:
                china_specific_in_scope_total += 1
            fetched_at = _text(row["fetched_at"])
            last_modified = _text(row["last_modified"])
            latest_fetched_at = max(latest_fetched_at, fetched_at)
            latest_modified_at = max(latest_modified_at, last_modified)
            scoped_records[ticket_id] = {
                "ticket_id": ticket_id,
                "name": _text(row["name"]),
                "description": _text(row["description"]),
                "creation_time": _text(row["creation_time"]),
                "last_modified": last_modified,
                "year": _year_from_record(row["year"], row["creation_time"]),
                "legacy_project": _text(row["legacy_project"]),
                "final_project": _text(row["final_project"]),
                "team": report_team,
                "solution_cluster": solution_cluster,
                "defect_category": defect_category,
                "product_areas": _text(row["product_areas"]),
                "function2modul": _text(row["function2modul"]),
                "function_responsible": _text(row["function_responsible"]),
                "assigned_ecu": _text(row["assigned_ecu"]),
                "ecu_to_modul": _text(row["ecu_to_modul"]),
                "software_version": _text(row["software_version"]),
                "blocking_reason": _text(row["blocking_reason"]),
                "reprel_changes": _text(row["reprel_changes"]),
                "severity": _text(row["severity"]),
                "problem_severity": _text(row["problem_severity"]),
                "reporting_class": _text(row["reporting_class"]),
                "parent_child_type": _text(row["parent_child_type"]),
                "relation_to": _text(row["relation_to"]),
                "comment_evidence": _comment_evidence(
                    row["comments"],
                    capture_comment_records=include_full_evidence,
                ),
                "direct_close": False,
                "seen_phase09": False,
                "latest_phase09_at": "",
                "reopened_after_phase09": False,
                "entered_phase06_after_phase09": False,
                "phase_history_count": 0,
                "audit_history_event_count": 0,
                "audit_history_events": [],
                "history_blocking_reason": "",
                "history_blocked_reason": "",
                "rejection_route": "",
                "target_version_events": [],
                "planning_window_events": [],
                "reprel_history_values": [],
                "postponement_document_events": [],
                "postponement_decision_date_events": [],
                "commercial_solution_date_events": [],
            }

        history_rows = _history_rows(
            connection,
            list(scoped_records),
            history_columns,
        )
        _append_history_evidence(
            scoped_records,
            history_rows,
            capture_audit_history=include_full_evidence,
        )
        relation_targets = {
            target
            for record in scoped_records.values()
            if (target := _extract_relation_target(record["relation_to"]))
        }
        parents = _parent_rows(connection, relation_targets, defect_columns)
        parent_history_rows = _history_rows(connection, list(parents), history_columns)
        parent_phase06_at = _latest_phase_entry_timestamps(
            parent_history_rows,
            phase_code="06",
        )
        for parent_id, parent in parents.items():
            parent["phase06_at"] = parent_phase06_at.get(parent_id, "")
    finally:
        connection.close()

    for record in scoped_records.values():
        _assess_record(record, parents)

    records = sorted(
        scoped_records.values(),
        key=lambda record: (
            -int(record["risk_score"]),
            str(record["risk_tier"]),
            str(record["ticket_id"]),
        ),
    )
    public_records = [
        _public_record(record, include_full_evidence=include_full_evidence)
        for record in records
    ]
    risk_tier_rows = _risk_tier_rows(records)
    risk_counts = {str(row["label"]): int(row["count"]) for row in risk_tier_rows}
    management_decision_total = sum(
        1
        for record in records
        if "management decision" in _normalized(record["effective_reason"])
    )
    management_decision_records = [
        record
        for record in records
        if "management decision" in _normalized(record["effective_reason"])
    ]
    history_reason_total = sum(
        1
        for record in records
        if record["reason_source"] in {"Phase-history blocking reason", "Phase-history blocked reason"}
    )
    closure_evidence = {
        "target_version_after_phase09_total": sum(
            1
            for record in records
            if record["target_version_status"] == "Target version recorded after current Phase 09"
        ),
        "target_version_before_phase09_total": sum(
            1
            for record in records
            if record["target_version_status"] == "Target version recorded before current Phase 09"
        ),
        "planning_window_after_phase09_total": sum(
            1
            for record in records
            if record["planning_window_status"] == "Planning window recorded after current Phase 09"
        ),
        "planning_window_before_phase09_total": sum(
            1
            for record in records
            if record["planning_window_status"] == "Planning window recorded before current Phase 09"
        ),
        "reprel_changes_positive_total": sum(
            1
            for record in records
            if isinstance(record["reprel_change_count"], int)
            and int(record["reprel_change_count"]) > 0
        ),
        "reprel_changes_zero_total": sum(
            1
            for record in records
            if record["reprel_change_count"] == 0
        ),
        "reprel_change_count_missing_total": sum(
            1
            for record in records
            if record["reprel_change_count"] is None
        ),
        "explicit_linked_resolution_after_phase09_total": sum(
            1
            for record in records
            if record["explicit_linked_resolution_after_phase09"]
        ),
        "explicit_linked_phase06_not_later_total": sum(
            1
            for record in records
            if record["related_resolution_status"]
            in {
                "Explicit linked ticket reached Phase 06 before current Phase 09",
                "Explicit linked ticket is Phase 06; timestamp unavailable",
            }
        ),
        "no_target_shift_or_explicit_resolution_total": sum(
            1
            for record in records
            if record["target_version_status"] == "No target version recorded"
            and record["planning_window_status"] == "No planning window recorded"
            and record["reprel_change_count"] in {None, 0}
            and record["repair_plan_status"] == "No repair-plan evidence"
            and not record["explicit_linked_resolution_after_phase09"]
        ),
        "no_remediation_or_linked_resolution_evidence_total": sum(
            1
            for record in records
            if not record["has_remediation_or_resolution_evidence"]
        ),
        "production_exposure_cannot_be_ruled_out_total": sum(
            1
            for record in records
            if record["production_exposure_status"].startswith("Production exposure cannot be ruled out")
        ),
        "priority_production_exposure_review_total": sum(
            1
            for record in records
            if record["production_exposure_status"] == "Production exposure cannot be ruled out - priority review"
        ),
    }

    return {
        "generated_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "source": {
            "db_path": str(resolved_db_path),
            "latest_fetched_at": latest_fetched_at,
            "latest_last_modified": latest_modified_at,
            "population_start": population_start,
            "population_end_exclusive": population_end_exclusive,
            "project_scope_field": project_scope_field,
            "project_scope_note": (
                "Final business project (tproject) is used for scope; legacy project is retained for audit."
                if project_scope_field == "tproject"
                else "Legacy project is used because this source database has no tproject column."
            ),
            "report_title": (
                "IDCEVO Phase 09 Closure Risk Review"
                if include_china_specific
                else "IDCEVO Phase 09 Global Closure Risk Review"
            ),
            "scope_label": (
                "Analyzed IDCEVO Phase 09"
                if include_china_specific
                else "Analyzed Global Phase 09"
            ),
            "scope_note": (
                "China-specific included: {} | CoC finder teams: {} excluded".format(
                    china_specific_in_scope_total,
                    coc_development_team_excluded_total,
                )
                if include_china_specific
                else "China-specific: {} | CoC finder teams: {} excluded".format(
                    china_excluded_total,
                    coc_development_team_excluded_total,
                )
            ),
            "scope_description": (
                "All IDCEVO tickets created from 2025-01-01 through the source snapshot are counted first. "
                "IDCEVO scope uses the final business project tproject. "
                "This analysis then selects their current Phase 09 tickets, includes China-specific functions and "
                "excludes tickets whose Problem Finder Team contains CoC."
                if include_china_specific
                else "All IDCEVO tickets created from 2025-01-01 through the source snapshot are counted first. "
                "IDCEVO scope uses the final business project tproject. "
                "This analysis then selects their current Phase 09 tickets, excludes China-specific functions and "
                "tickets raised by CoC development teams."
            ),
            "scope_rule": (
                "Use final business project tproject for IDCEVO scope when available; use legacy project only when tproject is unavailable. "
                "Count all scoped IDCEVO tickets in the creation-time window first, then select tickets whose current phase is 09. "
                + (
                    "Include China-specific functions. "
                    if include_china_specific
                    else "Exclude China-specific functions when Solution Cluster is China-specific; use Defect Category only when Solution Cluster is blank. "
                )
                + "Exclude tickets when Problem Finder Team contains CoC."
            ),
        },
        "summary": {
            "idcevo_all_ticket_total": idcevo_all_ticket_total,
            "idcevo_current_phase09_total": idcevo_phase09_total,
            "idcevo_phase09_total": idcevo_phase09_total,
            "china_excluded_total": china_excluded_total,
            "china_specific_in_scope_total": china_specific_in_scope_total,
            "coc_development_team_excluded_total": coc_development_team_excluded_total,
            "analyzed_phase09_total": len(records),
            "global_phase09_total": len(records),
            "history_coverage_total": sum(1 for record in records if record["phase_history_count"] > 0),
            "direct_close_total": sum(1 for record in records if record["direct_close"]),
            "governed_severe_total": sum(
                1 for record in records if record["is_governed_severe"]
            ),
            "needs_closure_evidence_review_total": sum(
                1 for record in records if record["needs_closure_evidence_review"]
            ),
            "needs_closure_evidence_review_governed_severe_total": sum(
                1
                for record in records
                if record["needs_closure_evidence_review"]
                and record["is_governed_severe"]
            ),
            "reopened_after_phase09_total": sum(1 for record in records if record["reopened_after_phase09"]),
            "entered_phase06_after_phase09_total": sum(1 for record in records if record["entered_phase06_after_phase09"]),
            "history_reason_total": history_reason_total,
            "management_decision_total": management_decision_total,
            "normal_closure_total": risk_counts.get("Normal closure evidence", 0),
            "repair_evidence_total": risk_counts.get("Repair plan / resolution evidence", 0),
            "priority_review_total": risk_counts.get("Priority production-exposure review", 0),
            "decision_conflict_total": risk_counts.get("Decision-evidence conflict", 0),
            "evidence_incomplete_total": risk_counts.get("Evidence incomplete / monitor", 0),
        },
        "risk_tiers": risk_tier_rows,
        "distributions": {
            "years": _counter_rows(records, "year", limit=10),
            "solution_clusters": _counter_rows(records, "solution_cluster", limit=40),
            "defect_categories": _counter_rows(records, "defect_category", limit=40),
            "teams": _counter_rows(records, "team", limit=40),
            "effective_reasons": _counter_rows(records, "effective_reason", limit=40),
            "repair_plan_statuses": _counter_rows(records, "repair_plan_status", limit=20),
            "severity": _counter_rows(records, "severity", limit=20),
            "problem_severity": _counter_rows(records, "problem_severity", limit=20),
            "reporting_classes": _counter_rows(records, "reporting_class", limit=30),
            "blocking_reason_groups": _counter_rows(records, "blocking_reason_group", limit=20),
            "closure_dispositions": _counter_rows(records, "closure_disposition", limit=20),
            "target_version_statuses": _counter_rows(records, "target_version_status", limit=10),
            "commercial_solution_date_statuses": _counter_rows(
                records, "commercial_solution_date_status", limit=10
            ),
            "postponement_document_statuses": _counter_rows(
                records, "postponement_document_status", limit=10
            ),
            "follow_up_statuses": _counter_rows(records, "follow_up_status", limit=10),
            "reprel_change_statuses": _counter_rows(records, "reprel_change_status", limit=30),
            "related_resolution_statuses": _counter_rows(records, "related_resolution_status", limit=20),
            "production_exposure_statuses": _counter_rows(records, "production_exposure_status", limit=20),
        },
        "closure_evidence": closure_evidence,
        "management_decision": {
            "total": management_decision_total,
            "priority_review_total": sum(
                1
                for record in management_decision_records
                if record["risk_tier"] == "Priority production-exposure review"
            ),
            "decision_conflict_total": sum(
                1
                for record in management_decision_records
                if record["risk_tier"] == "Decision-evidence conflict"
            ),
            "no_repair_evidence_total": sum(
                1
                for record in management_decision_records
                if record["repair_plan_status"] == "No repair-plan evidence"
            ),
            "reopened_after_phase09_total": sum(
                1
                for record in management_decision_records
                if record["reopened_after_phase09"]
            ),
            "risk_tiers": _risk_tier_rows(management_decision_records),
        },
        "tickets": public_records,
    }


def _build_html(payload: dict[str, object]) -> str:
    payload_json = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).replace("</", "<\\/")
    source = payload.get("source")
    source_data = source if isinstance(source, dict) else {}
    report_title = _text(source_data.get("report_title")) or "IDCEVO Phase 09 Closure Risk Review"
    scope_description = _text(source_data.get("scope_description"))
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>__REPORT_TITLE__</title>
  <style>
    :root{--ink:#172033;--muted:#5c687f;--line:#d9e0ea;--surface:#ffffff;--canvas:#edf2f7;--navy:#18365e;--blue:#1368ce;--green:#157a51;--amber:#a86700;--red:#bd2939;--gray:#607089}
    *{box-sizing:border-box} body{margin:0;background:var(--canvas);color:var(--ink);font-family:"Aptos","Segoe UI",sans-serif;font-size:14px;line-height:1.45}.wrap{width:min(1640px,calc(100% - 32px));margin:24px auto 36px}.hero{background:linear-gradient(120deg,#17355c,#1765a7);color:#fff;padding:26px 30px;border-radius:8px;box-shadow:0 12px 24px rgba(23,53,92,.18)}h1{margin:0;font-size:30px;letter-spacing:0}h2{margin:0 0 14px;font-size:19px;letter-spacing:0;color:var(--navy)}h3{margin:0 0 9px;font-size:15px;color:var(--navy)}.subtitle{margin:8px 0 0;color:#e2ecf8;max-width:1000px}.source{margin-top:10px;color:#d9e8f8;font-size:12px}.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin-top:18px}.kpi{border:1px solid rgba(255,255,255,.23);background:rgba(255,255,255,.1);min-height:102px;padding:13px}.kpi-label{color:#dceafa;font-size:12px}.kpi-value{font-size:28px;font-weight:700;margin-top:5px}.kpi-note{margin-top:3px;color:#dceafa;font-size:11px}.section{margin-top:16px;padding:18px;background:var(--surface);border:1px solid var(--line);border-radius:8px;box-shadow:0 4px 12px rgba(21,34,55,.04)}.grid-2{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:18px}.grid-3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px}.note{border-left:4px solid var(--blue);background:#eef6ff;padding:11px 13px;color:#334866}.warning{border-left-color:var(--amber);background:#fff8e8}.risk{border-left-color:var(--red);background:#fff0f1}.method{width:100%;border-collapse:collapse;font-size:12px}.method th,.method td{border:1px solid var(--line);padding:9px 10px;vertical-align:top;text-align:left}.method th{background:#eaf1fa;color:#203d63}.bar-row{display:grid;grid-template-columns:minmax(180px,280px) minmax(120px,1fr) 62px;gap:9px;align-items:center;margin:7px 0;font-size:12px}.bar-label{overflow-wrap:anywhere;font-weight:600;line-height:1.3}.bar-track{height:14px;background:#e5eaf1;overflow:hidden}.bar-fill{height:100%;background:var(--blue);min-width:2px}.bar-fill.green{background:var(--green)}.bar-fill.amber{background:var(--amber)}.bar-fill.red{background:var(--red)}.bar-value{text-align:right;color:var(--muted);font-variant-numeric:tabular-nums}.scroll{overflow:auto;border:1px solid var(--line)}table{border-collapse:collapse;width:100%;font-size:12px}th,td{border-bottom:1px solid var(--line);padding:8px 9px;text-align:left;vertical-align:top}th{position:sticky;top:0;background:#eaf1fa;color:#203d63;z-index:1;font-weight:700}tbody tr:hover{background:#f6f9fc}.filters{display:grid;grid-template-columns:repeat(5,minmax(140px,1fr));gap:10px;align-items:end;margin:12px 0}.field{display:flex;flex-direction:column;gap:4px}.field label{font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}.field select,.field input{height:34px;width:100%;border:1px solid #b9c5d4;background:#fff;padding:6px 8px;color:var(--ink);font:inherit}.command{height:34px;border:1px solid #0d579f;background:#1768b2;color:#fff;padding:6px 13px;font:inherit;cursor:pointer}.command:hover{background:#115895}.tier{display:inline-block;padding:3px 6px;border-radius:3px;font-size:11px;font-weight:700;line-height:1.2}.tier-priority-production-exposure-review{background:#ffe1e5;color:#931c2a}.tier-decision-evidence-conflict{background:#fff0cc;color:#7a4c00}.tier-evidence-incomplete-monitor{background:#e9edf2;color:#45546b}.tier-repair-plan-resolution-evidence{background:#dceeff;color:#125392}.tier-normal-closure-evidence{background:#daf3e6;color:#12613e}.muted{color:var(--muted)}.small{font-size:11px}.num{text-align:right;font-variant-numeric:tabular-nums}.flags{margin:0;padding-left:17px}.flags li{margin:0 0 3px}.evidence{white-space:normal;min-width:300px;max-width:480px}.pager{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-top:11px}.pager-actions{display:flex;gap:8px}.footer{margin:16px 0;color:var(--muted);font-size:12px}details summary{cursor:pointer;color:#155a9d;font-weight:700}.details-panel{margin-top:10px}.no-data{padding:16px;color:var(--muted);text-align:center}@media(max-width:1100px){.kpis{grid-template-columns:repeat(3,minmax(0,1fr))}.grid-3{grid-template-columns:1fr}.filters{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:720px){.wrap{width:min(100% - 20px,1640px);margin-top:10px}.hero{padding:20px}.kpis,.grid-2,.filters{grid-template-columns:1fr}h1{font-size:25px}.bar-row{grid-template-columns:110px 1fr 52px}.section{padding:14px}}
  </style>
</head>
<body>
  <main class="wrap">
    <header class="hero">
    <h1>__REPORT_TITLE__</h1>
        <p class="subtitle">__SCOPE_DESCRIPTION__</p>
      <div class="source" id="source-meta"></div>
      <div class="kpis" id="summary-cards"></div>
    </header>

    <section class="section">
      <h2>Scope and Interpretation</h2>
      <div class="grid-2">
        <div class="note"><strong>What this report can establish.</strong> It reconstructs current Phase 09, direct 01 to 09 closure, later reopening, reason evidence, duplicate-parent state, and repair-plan wording from the local Octane snapshot.</div>
        <div class="note warning"><strong>What it cannot establish alone.</strong> A Phase 09 decision is not proven incorrect solely by this data. "Priority" and "conflict" rows are evidence-based human-review candidates, not a claim that a defect reached production.</div>
      </div>
      <div class="details-panel"><table class="method"><thead><tr><th>Outcome label</th><th>Evidence rule</th><th>Interpretation</th></tr></thead><tbody>
        <tr><td>Normal closure evidence</td><td>Explicit benign reason with no elevated impact signal, or a child duplicate whose linked parent is currently Phase 06.</td><td>No independent customer-exposure evidence in this snapshot; this is not a field-quality guarantee.</td></tr>
        <tr><td>Repair plan / resolution evidence</td><td>Post-Phase-09 plan wording, later Phase 06 history, or a resolved duplicate parent.</td><td>There is remediation evidence, but a future plan is not proof that a release contains the fix.</td></tr>
        <tr><td>Priority production-exposure review</td><td>Score of 7 or more from serious severity/customer/safety/reporting signals, reopening, decision closure without repair evidence, or explicit no-fix wording.</td><td>Review the closure decision, target release, and customer impact before treating the risk as accepted.</td></tr>
        <tr><td>Decision-evidence conflict</td><td>A nominally benign reason conflicts with high-impact evidence and no repair evidence.</td><td>Validate the rationale, test evidence, and whether a parent ticket actually contains the remedy.</td></tr>
        <tr><td>Evidence incomplete / monitor</td><td>Neither a sufficient benign reason nor repair evidence was found.</td><td>Unknown is intentionally not labelled safe. Most of this cohort belongs here because closure fields are sparse.</td></tr>
      </tbody></table></div>
    </section>

    <section class="section">
      <h2>Function Distribution</h2>
      <div class="grid-2"><div><h3>Solution Cluster</h3><div id="solution-cluster-bars"></div></div><div><h3>Defect Category</h3><div id="defect-category-bars"></div></div></div>
      <div class="grid-2" style="margin-top:18px"><div><h3>Problem Finder Team</h3><div id="team-bars"></div></div><div><h3>Creation Year</h3><div id="year-bars"></div></div></div>
    </section>

    <section class="section">
      <h2>Closure, Repair, and Lifecycle Evidence</h2>
            <div class="grid-3"><div><h3>Effective Closure Reason</h3><div id="reason-bars"></div></div><div><h3>Blocking Reason Type</h3><div id="reason-group-bars"></div></div><div><h3>Risk Classification</h3><div id="tier-bars"></div></div></div>
            <div class="grid-2 details-panel"><div><h3>Severity</h3><div id="severity-bars"></div></div><div><h3>Problem Severity</h3><div id="problem-severity-bars"></div></div></div>
            <div class="details-panel note warning" id="management-decision-summary"></div>
      <div class="details-panel note risk"><strong>Direct closure is an audit signal, not a risk verdict.</strong> The report separately flags every exact 01 to 09 history transition. Reopened-after-09 and later-06 transitions are retained because they can disprove a simplistic "closed once, therefore safe" assumption.</div>
    </section>

        <section class="section">
            <h2>Audit Evidence</h2>
            <div class="grid-3"><div><h3>Closure Disposition</h3><div id="closure-disposition-bars"></div></div><div><h3>Assessment Document</h3><div id="assessment-document-bars"></div></div><div><h3>Follow-up Evidence</h3><div id="follow-up-bars"></div></div></div>
            <div class="details-panel note risk" id="closure-review-summary"></div>
        </section>

        <section class="section">
            <h2>Release, Shift, and Related Resolution Evidence</h2>
            <div class="grid-2"><div><h3>Target Repair Version</h3><div id="target-version-bars"></div></div><div><h3>Reprel Change / Shift Signal</h3><div id="reprel-bars"></div></div></div>
            <div class="grid-2 details-panel"><div><h3>Explicit Related Ticket Resolution</h3><div id="related-resolution-bars"></div></div><div><h3>Production Exposure Assessment</h3><div id="exposure-bars"></div></div></div>
            <div class="details-panel note warning"><strong>Interpretation boundary.</strong> A target-version field or a positive Reprel Changes count is evidence of recorded planning or change activity, not proof that a fix shipped. “Explicit linked ticket reached Phase 06 after current Phase 09” requires an Octane relation and a later Phase 06 timestamp. Where neither remediation nor explicit linked-resolution evidence exists, the report says production exposure cannot be ruled out; it does not assert that production exposure occurred.</div>
        </section>

    <section class="section">
      <h2>Ticket Evidence Explorer</h2>
      <div class="filters">
        <div class="field"><label for="risk-filter">Risk tier</label><select id="risk-filter"></select></div>
                <div class="field"><label for="exposure-filter">Exposure evidence</label><select id="exposure-filter"></select></div>
        <div class="field"><label for="year-filter">Year</label><select id="year-filter"></select></div>
        <div class="field"><label for="cluster-filter">Solution cluster</label><select id="cluster-filter"></select></div>
        <div class="field"><label for="direct-filter">Lifecycle</label><select id="direct-filter"><option value="all">All lifecycle paths</option><option value="direct">Direct 01 to 09</option><option value="reopened">Reopened after 09</option><option value="phase06">Reached 06 after 09</option></select></div>
        <div class="field"><label for="ticket-search">Ticket, title, reason</label><input id="ticket-search" type="search" /></div>
      </div>
    <div class="scroll"><table><thead><tr><th>Risk</th><th>Ticket</th><th>Function</th><th>Severity / classification</th><th>Closure and lifecycle</th><th>Target version / shift</th><th>Explicit related resolution</th><th>Exposure assessment</th><th>Review evidence</th></tr></thead><tbody id="ticket-body"></tbody></table></div>
      <div class="pager"><div class="muted small" id="ticket-count"></div><div class="pager-actions"><button class="command" type="button" id="previous-page">Previous</button><button class="command" type="button" id="next-page">Next</button></div></div>
    </section>
    <footer class="footer" id="footer-meta"></footer>
  </main>
  <script>
    const REPORT_DATA = __PAYLOAD_JSON__;
    const PAGE_SIZE = 100;
    const state = {riskTier:'all',exposure:'all',year:'all',cluster:'all',lifecycle:'all',search:'',page:0};
    function escapeHtml(value){return String(value ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;');}
    function fmt(value){return Number(value || 0).toLocaleString();}
    function tierClass(value){return 'tier-'+String(value || '').toLowerCase().replaceAll(/[^a-z0-9]+/g,'-').replace(/^[-]+|[-]+$/g,'');}
    function renderCards(){const s=REPORT_DATA.summary;const closure=REPORT_DATA.closure_evidence;const cards=[['All IDCEVO tickets',s.idcevo_all_ticket_total,'Created from '+REPORT_DATA.source.population_start+' to source snapshot'],['Current Phase 09',s.idcevo_current_phase09_total,'Subset of all IDCEVO tickets'],['Analyzed Global Phase 09',s.global_phase09_total,'China-specific: '+fmt(s.china_excluded_total)+' | CoC development teams: '+fmt(s.coc_development_team_excluded_total)+' excluded'],['Target version after 09',closure.target_version_after_phase09_total,'Structured repair-target evidence'],['Reprel changes > 0',closure.reprel_changes_positive_total,'Recorded change/shift signal'],['Linked Phase 06 later',closure.explicit_linked_resolution_after_phase09_total,'Explicit relation plus later resolution'],['No target / shift / link',closure.no_target_shift_or_explicit_resolution_total,'No recorded target, planning, Reprel shift, or linked resolution'],['Exposure not ruled out',closure.production_exposure_cannot_be_ruled_out_total,'No closure evidence with elevated risk'],['Priority review',s.priority_review_total,'Potential production-exposure candidates'],['Decision conflict',s.decision_conflict_total,'Closure rationale needs validation'],['Evidence incomplete',s.evidence_incomplete_total,'Not classified as safe']];document.getElementById('summary-cards').innerHTML=cards.map(card=>`<div class="kpi"><div class="kpi-label">${escapeHtml(card[0])}</div><div class="kpi-value">${fmt(card[1])}</div><div class="kpi-note">${escapeHtml(card[2])}</div></div>`).join('');}
    function renderBars(containerId,rows,kind){const container=document.getElementById(containerId);if(!rows.length){container.innerHTML='<div class="no-data">No data</div>';return;}const maximum=Math.max(1,...rows.map(row=>Number(row.count||0)));container.innerHTML=rows.map(row=>{const color=kind==='tier'?(String(row.label).startsWith('Priority')?'red':String(row.label).startsWith('Decision')?'amber':String(row.label).startsWith('Normal')?'green':'') : '';return `<div class="bar-row"><div class="bar-label" title="${escapeHtml(row.label)}">${escapeHtml(row.label)}</div><div class="bar-track"><div class="bar-fill ${color}" style="width:${Math.max(2,(Number(row.count||0)/maximum)*100)}%"></div></div><div class="bar-value">${fmt(row.count)}</div></div>`;}).join('');}
    function populateSelect(id,values,allLabel){const select=document.getElementById(id);select.innerHTML=[`<option value="all">${escapeHtml(allLabel)}</option>`,...values.map(value=>`<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`)].join('');}
    function initialise(){const d=REPORT_DATA.distributions;const management=REPORT_DATA.management_decision;const closure=REPORT_DATA.closure_evidence;renderCards();renderBars('solution-cluster-bars',d.solution_clusters,'distribution');renderBars('defect-category-bars',d.defect_categories,'distribution');renderBars('team-bars',d.teams,'distribution');renderBars('year-bars',d.years,'distribution');renderBars('reason-bars',d.effective_reasons,'distribution');renderBars('reason-group-bars',d.blocking_reason_groups,'distribution');renderBars('severity-bars',d.severity,'distribution');renderBars('problem-severity-bars',d.problem_severity,'distribution');renderBars('tier-bars',REPORT_DATA.risk_tiers,'tier');renderBars('target-version-bars',d.target_version_statuses,'distribution');renderBars('reprel-bars',d.reprel_change_statuses,'distribution');renderBars('related-resolution-bars',d.related_resolution_statuses,'distribution');renderBars('exposure-bars',d.production_exposure_statuses,'tier');populateSelect('risk-filter',REPORT_DATA.risk_tiers.map(row=>row.label),'All risk tiers');populateSelect('exposure-filter',[...new Set(REPORT_DATA.tickets.map(ticket=>ticket.production_exposure_status))].sort(),'All exposure assessments');populateSelect('year-filter',[...new Set(REPORT_DATA.tickets.map(ticket=>ticket.year))].sort(),'All years');populateSelect('cluster-filter',[...new Set(REPORT_DATA.tickets.map(ticket=>ticket.solution_cluster))].sort((left,right)=>left.localeCompare(right)),'All solution clusters');document.getElementById('management-decision-summary').textContent=`Management-decision audit: ${fmt(management.total)} Global tickets; ${fmt(management.no_repair_evidence_total)} have no repair-plan evidence; ${fmt(management.priority_review_total)} are priority production-exposure reviews; ${fmt(management.reopened_after_phase09_total)} were reopened after Phase 09. Release/shift evidence: ${fmt(closure.target_version_after_phase09_total)} target versions recorded after Phase 09; ${fmt(closure.reprel_changes_positive_total)} tickets with Reprel changes above zero.`;const source=REPORT_DATA.source;document.getElementById('source-meta').textContent=`Generated ${REPORT_DATA.generated_at} | source snapshot: ${source.db_path} | latest fetch: ${source.latest_fetched_at || 'not recorded'}`;document.getElementById('footer-meta').textContent=`Scope rule: ${source.scope_rule} Last source modification observed: ${source.latest_last_modified || 'not recorded'}. Reprel Changes is a change-count signal, not proof that a repair version shipped.`;}
    function filteredTickets(){const query=state.search.trim().toLowerCase();return REPORT_DATA.tickets.filter(ticket=>{if(state.riskTier!=='all'&&ticket.risk_tier!==state.riskTier)return false;if(state.exposure!=='all'&&ticket.production_exposure_status!==state.exposure)return false;if(state.year!=='all'&&ticket.year!==state.year)return false;if(state.cluster!=='all'&&ticket.solution_cluster!==state.cluster)return false;if(state.lifecycle==='direct'&&!ticket.direct_close)return false;if(state.lifecycle==='reopened'&&!ticket.reopened_after_phase09)return false;if(state.lifecycle==='phase06'&&!ticket.entered_phase06_after_phase09)return false;if(query){const searchable=[ticket.ticket_id,ticket.name,ticket.effective_reason,ticket.blocking_reason_group,ticket.solution_cluster,ticket.defect_category,ticket.target_version_value,ticket.related_resolution_ticket_id,ticket.risk_flags.join(' ')].join(' ').toLowerCase();if(!searchable.includes(query))return false;}return true;});}
    function renderTickets(){const records=filteredTickets();const pageCount=Math.max(1,Math.ceil(records.length/PAGE_SIZE));state.page=Math.min(state.page,pageCount-1);const visible=records.slice(state.page*PAGE_SIZE,(state.page+1)*PAGE_SIZE);const body=document.getElementById('ticket-body');body.innerHTML=visible.map(ticket=>{const parent=ticket.duplicate_parent_id?`${ticket.duplicate_parent_id}${ticket.duplicate_parent_phase?' / '+ticket.duplicate_parent_phase:''}`:'-';const targetVersion=ticket.target_version_value?`${ticket.target_version_status}: ${ticket.target_version_value}`:ticket.target_version_status;const planningWindow=ticket.planning_window_value?`${ticket.planning_window_status}: ${ticket.planning_window_value}`:ticket.planning_window_status;const relatedTicket=ticket.related_resolution_ticket_id?`${ticket.related_resolution_ticket_id}${ticket.related_resolution_phase06_at?' / '+ticket.related_resolution_phase06_at:''}`:'-';const flags=ticket.risk_flags.length?`<ul class="flags">${ticket.risk_flags.map(flag=>`<li>${escapeHtml(flag)}</li>`).join('')}</ul>`:'-';const evidence=[ticket.decision_evidence_excerpt,ticket.plan_evidence_excerpt,ticket.no_fix_evidence_excerpt].filter(Boolean);return `<tr><td><span class="tier ${tierClass(ticket.risk_tier)}">${escapeHtml(ticket.risk_tier)}</span><div class="small muted">score ${fmt(ticket.risk_score)}</div></td><td><strong>${escapeHtml(ticket.ticket_id)}</strong><div>${escapeHtml(ticket.name)}</div><div class="small muted">${escapeHtml(ticket.year)} | ${escapeHtml(ticket.creation_time)}</div></td><td>${escapeHtml(ticket.solution_cluster)}<div class="small muted">${escapeHtml(ticket.defect_category)}</div></td><td>${escapeHtml(ticket.severity)}<div class="small muted">${escapeHtml(ticket.problem_severity)}</div><div class="small muted">${escapeHtml(ticket.reporting_class)}</div></td><td>${escapeHtml(ticket.effective_reason)}<div class="small muted">${escapeHtml(ticket.blocking_reason_group)}</div><div class="small muted">${ticket.direct_close?'Direct 01 to 09':'Not direct'} | ${ticket.reopened_after_phase09?'reopened after 09':'not reopened'}</div><div class="small muted">Duplicate parent: ${escapeHtml(parent)}</div></td><td>${escapeHtml(targetVersion)}<div class="small muted">${escapeHtml(planningWindow)}</div><div class="small muted">${escapeHtml(ticket.reprel_change_status)}</div></td><td>${escapeHtml(ticket.related_resolution_status)}<div class="small muted">Linked ticket: ${escapeHtml(relatedTicket)}</div></td><td>${escapeHtml(ticket.production_exposure_status)}<div class="small muted">${escapeHtml(ticket.repair_plan_status)}</div></td><td class="evidence">${flags}<details><summary>Source excerpts</summary><div class="details-panel small"><strong>Description:</strong> ${escapeHtml(ticket.description_excerpt || '-')}<br/><strong>Matched evidence:</strong> ${escapeHtml(evidence.join(' | ') || '-')}</div></details></td></tr>`;}).join('')||'<tr><td colspan="9" class="no-data">No tickets match the current filters.</td></tr>';document.getElementById('ticket-count').textContent=`${fmt(records.length)} matching tickets | page ${fmt(state.page+1)} of ${fmt(pageCount)} | ${fmt(visible.length)} shown`;document.getElementById('previous-page').disabled=state.page===0;document.getElementById('next-page').disabled=state.page>=pageCount-1;}
    function bind(){const bindings=[['risk-filter','riskTier'],['exposure-filter','exposure'],['year-filter','year'],['cluster-filter','cluster'],['direct-filter','lifecycle']];bindings.forEach(([id,key])=>document.getElementById(id).addEventListener('change',event=>{state[key]=event.target.value;state.page=0;renderTickets();}));document.getElementById('ticket-search').addEventListener('input',event=>{state.search=event.target.value;state.page=0;renderTickets();});document.getElementById('previous-page').addEventListener('click',()=>{state.page=Math.max(0,state.page-1);renderTickets();});document.getElementById('next-page').addEventListener('click',()=>{state.page+=1;renderTickets();});}
        function renderClosureAudit(){const d=REPORT_DATA.distributions;const s=REPORT_DATA.summary;renderBars('closure-disposition-bars',d.closure_dispositions,'tier');renderBars('assessment-document-bars',d.postponement_document_statuses,'distribution');renderBars('follow-up-bars',d.follow_up_statuses,'distribution');const cards=[...document.querySelectorAll('.kpi')];const scopeCard=cards.find(card=>card.querySelector('.kpi-label')?.textContent==='Analyzed Global Phase 09');if(scopeCard){scopeCard.querySelector('.kpi-label').textContent=REPORT_DATA.source.scope_label;scopeCard.querySelector('.kpi-note').textContent=REPORT_DATA.source.scope_note;}document.getElementById('closure-review-summary').textContent=`${fmt(s.needs_closure_evidence_review_total)} direct closures have no auditable rationale or follow-up; ${fmt(s.needs_closure_evidence_review_governed_severe_total)} meet the governed severe definition (Showstopper Candidate/Confirmed plus BI 04/05/06). They are retained as review candidates, not asserted field escapes.`;}
        initialise();renderClosureAudit();bind();renderTickets();
  </script>
</body>
</html>""".replace("__PAYLOAD_JSON__", payload_json).replace(
                "__REPORT_TITLE__", html_escape(report_title)
        ).replace("__SCOPE_DESCRIPTION__", html_escape(scope_description))


def generate_phase09_idcevo_report(
    *,
    db_path: str | Path | None = None,
    output_path: str | Path,
    include_china_specific: bool = False,
) -> Path:
    payload = build_phase09_idcevo_report_payload(
        db_path=db_path,
        include_china_specific=include_china_specific,
    )
    resolved_output_path = Path(output_path)
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_output_path.write_text(_build_html(payload), encoding="utf-8")
    return resolved_output_path


# ---------------------------------------------------------------------------
# Phase 06 (resolved / concluded tickets) resolution-quality review
# ---------------------------------------------------------------------------


def _assess_release_evidence_phase06(record: dict[str, Any]) -> None:
    """Phase 06 version of release-evidence assessment.

    Uses ``latest_phase06_at`` instead of ``latest_phase09_at`` for temporal
    comparisons.  Status labels reflect "current Phase 06" rather than 09.
    """
    phase06_at = _text(record["latest_phase06_at"])
    target_version_events = list(record["target_version_events"])
    post_phase06_target_events = [
        event
        for event in target_version_events
        if _is_after_or_at(event[0], phase06_at)
    ]
    target_timestamp, target_field, target_value = _latest_event(
        post_phase06_target_events or target_version_events
    )
    record["target_version_value"] = target_value
    record["target_version_field"] = target_field
    record["target_version_at"] = target_timestamp
    if post_phase06_target_events:
        record["target_version_status"] = "Target version recorded after current Phase 06"
    elif target_version_events:
        record["target_version_status"] = "Target version recorded before current Phase 06"
    else:
        record["target_version_status"] = "No target version recorded"

    planning_window_events = list(record["planning_window_events"])
    post_phase06_window_events = [
        event
        for event in planning_window_events
        if _is_after_or_at(event[0], phase06_at)
    ]
    window_timestamp, window_field, window_value = _latest_event(
        post_phase06_window_events or planning_window_events
    )
    record["planning_window_value"] = window_value
    record["planning_window_field"] = window_field
    record["planning_window_at"] = window_timestamp
    if post_phase06_window_events:
        record["planning_window_status"] = "Planning window recorded after current Phase 06"
    elif planning_window_events:
        record["planning_window_status"] = "Planning window recorded before current Phase 06"
    else:
        record["planning_window_status"] = "No planning window recorded"

    reprel_count = _parse_reprel_change_count(record["reprel_changes"])
    reprel_source = "Current Reprel Changes"
    if reprel_count is None:
        for _timestamp, raw_value in reversed(record["reprel_history_values"]):
            reprel_count = _parse_reprel_change_count(raw_value)
            if reprel_count is not None:
                reprel_source = "Reprel Changes history"
                break
    record["reprel_change_count"] = reprel_count
    record["reprel_change_source"] = reprel_source if reprel_count is not None else ""
    if reprel_count is None:
        record["reprel_change_status"] = "Reprel change count not recorded"
    elif reprel_count == 0:
        record["reprel_change_status"] = "No recorded Reprel changes: 0"
    else:
        record["reprel_change_status"] = f"Recorded Reprel changes: {reprel_count}"

    commercial_solution_date_events = list(record["commercial_solution_date_events"])
    post_phase06_commercial_solution_date_events = [
        event
        for event in commercial_solution_date_events
        if _is_after_or_at(event[0], phase06_at)
    ]
    commercial_timestamp, commercial_field, commercial_value = _latest_event(
        post_phase06_commercial_solution_date_events or commercial_solution_date_events
    )
    record["commercial_solution_date_value"] = commercial_value
    record["commercial_solution_date_field"] = commercial_field
    record["commercial_solution_date_at"] = commercial_timestamp
    if post_phase06_commercial_solution_date_events:
        record["commercial_solution_date_status"] = (
            "Commercial solution date recorded after current Phase 06"
        )
    elif commercial_solution_date_events:
        record["commercial_solution_date_status"] = (
            "Commercial solution date recorded before current Phase 06"
        )
    else:
        record["commercial_solution_date_status"] = "No commercial solution date recorded"

    postponement_decision_date_events = list(record["postponement_decision_date_events"])
    postponement_timestamp, postponement_field, postponement_value = _latest_event(
        postponement_decision_date_events
    )
    record["postponement_decision_date_value"] = postponement_value
    record["postponement_decision_date_field"] = postponement_field
    record["postponement_decision_date_at"] = postponement_timestamp
    record["postponement_decision_date_status"] = (
        "Postponement/tolerance decision date recorded"
        if postponement_decision_date_events
        else "No postponement/tolerance decision date recorded"
    )


def _assess_related_resolution_phase06(
    record: dict[str, Any],
    parents: dict[str, dict[str, str]],
) -> None:
    """Phase 06 version of related-resolution assessment.

    Compares parent Phase 06 timestamps against the ticket's own
    ``latest_phase06_at`` instead of 09.
    """
    relation_target = _extract_relation_target(record["relation_to"])
    parent = parents.get(relation_target, {})
    parent_phase = _text(parent.get("phase"))
    parent_phase06_at = _text(parent.get("phase06_at"))
    phase06_at = _text(record["latest_phase06_at"])
    record["duplicate_parent_id"] = relation_target
    record["duplicate_parent_phase"] = parent_phase
    record["duplicate_parent_name"] = _text(parent.get("name"))
    record["related_resolution_ticket_id"] = relation_target
    record["related_resolution_phase06_at"] = parent_phase06_at

    if not relation_target:
        related_resolution_status = "No explicit linked ticket"
    elif not parent:
        related_resolution_status = "Explicit linked ticket unavailable in source"
    elif not _phase_is_resolved(parent_phase):
        related_resolution_status = "Explicit linked ticket is not currently Phase 06"
    elif phase06_at and parent_phase06_at and parent_phase06_at >= phase06_at:
        related_resolution_status = "Explicit linked ticket reached Phase 06 after current Phase 06"
    elif parent_phase06_at:
        related_resolution_status = "Explicit linked ticket reached Phase 06 before current Phase 06"
    else:
        related_resolution_status = "Explicit linked ticket is Phase 06; timestamp unavailable"
    record["related_resolution_status"] = related_resolution_status
    record["explicit_linked_resolution_after_phase06"] = (
        related_resolution_status == "Explicit linked ticket reached Phase 06 after current Phase 06"
    )


def _assess_record_phase06(
    record: dict[str, Any],
    parents: dict[str, dict[str, str]],
) -> None:
    effective_reason, reason_source = _reason_details(record)
    record["effective_reason"] = effective_reason
    record["reason_source"] = reason_source
    record["blocking_reason_group"] = _blocking_reason_group(effective_reason)
    _assess_related_resolution_phase06(record, parents)
    _assess_postponement_document(record)
    has_resolved_duplicate_parent = (
        _normalized(effective_reason) == "child (duplicate)"
        and _phase_is_resolved(record["duplicate_parent_phase"])
    )
    record["has_resolved_duplicate_parent"] = has_resolved_duplicate_parent

    _assess_release_evidence_phase06(record)

    comment_evidence = record["comment_evidence"]
    latest_phase06_at = _text(record["latest_phase06_at"])
    record["plan_evidence_excerpt"] = _first_excerpt(comment_evidence["plan_records"])
    record["decision_evidence_excerpt"] = _first_excerpt(
        comment_evidence["decision_records"] or comment_evidence["ticket_reduction_records"]
    )
    record["no_fix_evidence_excerpt"] = _first_excerpt(comment_evidence["no_fix_records"])

    # Phase 06 tickets ARE resolved — assess repair-plan quality accordingly.
    if comment_evidence["plan_records"]:
        repair_plan_status = "Resolution recorded (Phase 06 concluded)"
    elif has_resolved_duplicate_parent:
        repair_plan_status = "Linked duplicate parent is Phase 06"
    elif comment_evidence["decision_records"] or comment_evidence["ticket_reduction_records"]:
        repair_plan_status = "Resolution via decision/tolerance evidence"
    else:
        repair_plan_status = "Resolution evidence incomplete"
    record["repair_plan_status"] = repair_plan_status

    has_reprel_shift_evidence = (
        isinstance(record["reprel_change_count"], int)
        and int(record["reprel_change_count"]) > 0
    )
    has_target_or_planning_evidence = (
        record["target_version_status"] != "No target version recorded"
        or record["planning_window_status"] != "No planning window recorded"
    )
    has_commercial_solution_evidence = (
        record["commercial_solution_date_status"]
        != "No commercial solution date recorded"
    )
    has_repair_plan_evidence = repair_plan_status not in {
        "Resolution evidence incomplete",
    }
    has_related_resolution_evidence = record["explicit_linked_resolution_after_phase06"]
    has_follow_up_evidence = (
        has_target_or_planning_evidence
        or has_commercial_solution_evidence
        or has_repair_plan_evidence
        or has_related_resolution_evidence
        or has_reprel_shift_evidence
    )
    record["has_follow_up_evidence"] = has_follow_up_evidence
    if has_related_resolution_evidence:
        record["follow_up_status"] = "Resolution evidence recorded"
    elif has_target_or_planning_evidence:
        record["follow_up_status"] = "Target version or planning window recorded"
    elif has_commercial_solution_evidence:
        record["follow_up_status"] = "Commercial solution date recorded"
    elif has_repair_plan_evidence:
        record["follow_up_status"] = "Repair plan reference recorded"
    elif has_reprel_shift_evidence:
        record["follow_up_status"] = "Reprel change / shift recorded"
    else:
        record["follow_up_status"] = "No remediation or follow-up evidence"

    flags: list[str] = []
    record["is_governed_severe"] = _is_governed_severe(record)
    score = _severity_points(record, flags)
    serious_impact = score >= 3
    reason_normalized = _normalized(effective_reason)
    decision_closure = (
        reason_normalized in DECISION_CLOSURE_REASONS
        or "management decision" in reason_normalized
    )
    record["decision_closure"] = decision_closure

    if record["reopened_after_phase06"]:
        score += 3
        flags.append("Reopened after an earlier Phase 06 resolution")
    if comment_evidence["no_fix_records"]:
        score += 3
        flags.append("Comment states no fix or no repair")
    if decision_closure and repair_plan_status == "Resolution evidence incomplete":
        score += 2
        flags.append("Decision/tolerance closure without repair evidence")
    if effective_reason == "Not recorded":
        score += 1
        flags.append("No structured closure reason")
    if comment_evidence["ticket_reduction_records"] and repair_plan_status == "Resolution evidence incomplete":
        score += 1
        flags.append("Generic ticket-reduction decision wording")
    if reason_normalized == "child (duplicate)" and not has_resolved_duplicate_parent:
        score += 1
        flags.append("Linked duplicate parent is unavailable or not resolved")

    normal_reason = reason_normalized in NORMAL_CLOSURE_REASONS
    normal_resolution = (
        (normal_reason or has_resolved_duplicate_parent)
        and not serious_impact
        and not record["reopened_after_phase06"]
        and not comment_evidence["no_fix_records"]
    )
    record["decision_closure_without_follow_up"] = (
        decision_closure and not record["has_follow_up_evidence"]
    )
    # Phase 06 does not use needs_closure_evidence_review — always False.
    record["needs_closure_evidence_review"] = False

    decision_evidence_conflict = (
        normal_reason
        and serious_impact
        and repair_plan_status == "Resolution evidence incomplete"
    )
    record["decision_evidence_conflict"] = decision_evidence_conflict

    # Phase 06 risk tiers reflect RESOLUTION QUALITY, not closure-evidence gaps.
    if decision_evidence_conflict:
        risk_tier = "Root-cause quality concern"
    elif record["reopened_after_phase06"] or comment_evidence["no_fix_records"]:
        risk_tier = "Field-escape risk flagged"
    elif score >= 7 and repair_plan_status == "Resolution evidence incomplete":
        risk_tier = "Resolution evidence incomplete"
    elif not has_follow_up_evidence and serious_impact:
        risk_tier = "Resolution with residual risk"
    elif normal_resolution:
        risk_tier = "Effective resolution (verified)"
    elif repair_plan_status != "Resolution evidence incomplete":
        risk_tier = "Effective resolution (verified)"
    else:
        risk_tier = "Resolution evidence incomplete"

    if record["direct_close"]:
        flags.append("Direct lifecycle path: 01 -> 06")
    has_remediation_or_resolution_evidence = has_follow_up_evidence
    record["has_remediation_or_resolution_evidence"] = has_remediation_or_resolution_evidence

    # Phase 06 production exposure reflects resolution / fix verification quality.
    if not has_remediation_or_resolution_evidence and (
        record["reopened_after_phase06"] or comment_evidence["no_fix_records"]
    ):
        production_exposure_status = "Resolved but field-escape risk remains"
    elif not has_remediation_or_resolution_evidence and serious_impact:
        production_exposure_status = "Resolved without root-cause verification"
    elif has_remediation_or_resolution_evidence and not record["reopened_after_phase06"]:
        production_exposure_status = "Resolved with verified fix"
    else:
        production_exposure_status = "Resolved without root-cause verification"
    record["risk_score"] = score
    record["risk_tier"] = risk_tier
    record["risk_flags"] = flags
    record["production_exposure_status"] = production_exposure_status

    if normal_resolution:
        closure_disposition = "Resolution evidence complete"
    elif record["has_follow_up_evidence"]:
        closure_disposition = "Resolution with follow-up needed"
    elif decision_closure and record["has_substantive_assessment_document"]:
        closure_disposition = "Documented risk acceptance without delivery evidence"
    elif decision_closure:
        closure_disposition = "Decision / tolerance without delivery evidence"
    else:
        closure_disposition = "Resolution evidence incomplete"
    record["closure_disposition"] = closure_disposition
    record["description_excerpt"] = _description_excerpt(record["description"])
    record["risk_basis"] = _risk_basis_phase06(record)
    record["automated_analysis"] = _automated_evidence_analysis_phase06(record)
    record["evidence_index"] = _evidence_index(record)


def _risk_basis_phase06(record: dict[str, Any]) -> str:
    qgate_summary = "{} / {}".format(
        _display(record["reporting_class"]),
        _display(record["problem_severity"]),
    )
    target_detail = _text(record["target_version_value"]) or "Not recorded"
    planning_detail = _text(record["planning_window_value"]) or "Not recorded"
    commercial_detail = _text(record["commercial_solution_date_value"]) or "Not recorded"
    risk_flags = record["risk_flags"]
    flag_text = "; ".join(str(flag) for flag in risk_flags) if risk_flags else "None"
    return "\n".join(
        (
            "风险层级：{}；风险分：{}。".format(
                _display(record["risk_tier"]), record["risk_score"]
            ),
            "QGate 分类 / BI：{}；QGate 严重：{}。".format(
                qgate_summary,
                "是" if record["is_governed_severe"] else "否",
            ),
            "解决处置：{}。".format(_display(record["closure_disposition"])),
            "关闭理由：{}（{}）。".format(
                _display(record["effective_reason"]), _display(record["reason_source"])
            ),
            "后续跟进：{}；Target：{}；计划窗口：{}；商业解决日期：{}；{}。".format(
                _display(record["follow_up_status"]),
                target_detail,
                planning_detail,
                commercial_detail,
                _display(record["reprel_change_status"]),
            ),
            "延期/容忍文档：{}；关联解决：{}。".format(
                _display(record["postponement_document_status"]),
                _display(record["related_resolution_status"]),
            ),
            "风险标记：{}。".format(flag_text),
            "解决/现场判断：{}。".format(
                _display(record["production_exposure_status"])
            ),
        )
    )


def _automated_evidence_analysis_phase06(record: dict[str, Any]) -> str:
    description = _readable_excerpt(record["description"])
    issue_summary = description or "源 ticket 未记录问题描述。"
    lifecycle = (
        "历史存在直接生命周期 01 -> 06。"
        if record["direct_close"]
        else "历史未发现直接生命周期 01 -> 06。"
    )
    if record["reopened_after_phase06"]:
        lifecycle += " 此前 Phase 06 后曾重开。"
    rejection_route = _text(record["rejection_route"]) or "Not recorded"
    comment_evidence = record["comment_evidence"]
    comment_summary = "评论 {} 条".format(comment_evidence["comment_count"])
    if record["plan_evidence_excerpt"]:
        comment_summary += "；发现修复计划评论摘录：{}".format(
            record["plan_evidence_excerpt"]
        )
    if record["decision_evidence_excerpt"]:
        comment_summary += "；发现决策评论摘录：{}".format(
            record["decision_evidence_excerpt"]
        )
    if record["no_fix_evidence_excerpt"]:
        comment_summary += "；发现无修复评论摘录：{}".format(
            record["no_fix_evidence_excerpt"]
        )
    return "\n".join(
        (
            "问题概述：{}。{}".format(_display(record["name"]), issue_summary),
            "解决事实：当前为 Phase 06；{} 有效关闭理由为 {}（{}）；拒绝路径为 {}。".format(
                lifecycle,
                _display(record["effective_reason"]),
                _display(record["reason_source"]),
                rejection_route,
            ),
            "处置与关联：{} Target 版本：{}；计划窗口：{}；商业解决日期：{}；Reprel/Shift：{}；延期文档：{}；关联票：{}。".format(
                _display(record["follow_up_status"]),
                _text(record["target_version_value"]) or "Not recorded",
                _text(record["planning_window_value"]) or "Not recorded",
                _text(record["commercial_solution_date_value"]) or "Not recorded",
                _display(record["reprel_change_status"]),
                _display(record["postponement_document_status"]),
                _display(record["related_resolution_status"]),
            ),
            "评论与分析证据：{}。".format(comment_summary),
            "自动结论：{} {}".format(
                _display(record["closure_disposition"]),
                _display(record["production_exposure_status"]),
            ),
        )
    )


def _public_record_phase06(
    record: dict[str, Any],
    *,
    include_full_evidence: bool = False,
) -> dict[str, object]:
    public_record: dict[str, object] = {
        "ticket_id": record["ticket_id"],
        "name": record["name"],
        "year": record["year"],
        "creation_time": record["creation_time"],
        "last_modified": record["last_modified"],
        "team": record["team"],
        "solution_cluster": _display(record["solution_cluster"], "(blank)"),
        "defect_category": _display(record["defect_category"], "(blank)"),
        "product_areas": _display(record["product_areas"], "(blank)"),
        "function2modul": _display(record["function2modul"], "(blank)"),
        "function_responsible": _display(record["function_responsible"], "(blank)"),
        "assigned_ecu": _display(record["assigned_ecu"], "(blank)"),
        "ecu_to_modul": _display(record["ecu_to_modul"], "(blank)"),
        "legacy_project": _display(record["legacy_project"], "(blank)"),
        "final_project": _display(record["final_project"], "(blank)"),
        "software_version": _display(record["software_version"], "(blank)"),
        "parent_child_type": _display(record["parent_child_type"], "(blank)"),
        "relation_to": _display(record["relation_to"], "(blank)"),
        "severity": _display(record["severity"], "(blank)"),
        "problem_severity": _display(record["problem_severity"], "(blank)"),
        "reporting_class": _display(record["reporting_class"], "(blank)"),
        "is_governed_severe": bool(record["is_governed_severe"]),
        "effective_reason": record["effective_reason"],
        "reason_source": record["reason_source"],
        "blocking_reason_group": record["blocking_reason_group"],
        "rejection_route": _display(record["rejection_route"], "(blank)"),
        "direct_close": bool(record["direct_close"]),
        "latest_phase06_at": record["latest_phase06_at"],
        "reopened_after_phase06": bool(record["reopened_after_phase06"]),
        "repair_plan_status": record["repair_plan_status"],
        "reprel_changes": _display(record["reprel_changes"], "(blank)"),
        "target_version_status": record["target_version_status"],
        "target_version_value": record["target_version_value"],
        "target_version_field": record["target_version_field"],
        "target_version_at": record["target_version_at"],
        "planning_window_status": record["planning_window_status"],
        "planning_window_value": record["planning_window_value"],
        "planning_window_field": record["planning_window_field"],
        "planning_window_at": record["planning_window_at"],
        "reprel_change_count": record["reprel_change_count"],
        "reprel_change_source": record["reprel_change_source"],
        "reprel_change_status": record["reprel_change_status"],
        "commercial_solution_date_status": record["commercial_solution_date_status"],
        "commercial_solution_date_value": record["commercial_solution_date_value"],
        "commercial_solution_date_field": record["commercial_solution_date_field"],
        "commercial_solution_date_at": record["commercial_solution_date_at"],
        "postponement_decision_date_status": record["postponement_decision_date_status"],
        "postponement_decision_date_value": record["postponement_decision_date_value"],
        "postponement_decision_date_field": record["postponement_decision_date_field"],
        "postponement_decision_date_at": record["postponement_decision_date_at"],
        "follow_up_status": record["follow_up_status"],
        "has_follow_up_evidence": bool(record["has_follow_up_evidence"]),
        "decision_closure_without_follow_up": bool(
            record["decision_closure_without_follow_up"]
        ),
        "needs_closure_evidence_review": False,
        "closure_disposition": record["closure_disposition"],
        "postponement_document_status": record["postponement_document_status"],
        "postponement_document_at": record["postponement_document_at"],
        "postponement_document_excerpt": record["postponement_document_excerpt"],
        "has_substantive_assessment_document": bool(
            record["has_substantive_assessment_document"]
        ),
        "duplicate_parent_id": record["duplicate_parent_id"],
        "duplicate_parent_phase": record["duplicate_parent_phase"],
        "duplicate_parent_name": record["duplicate_parent_name"],
        "related_resolution_status": record["related_resolution_status"],
        "related_resolution_ticket_id": record["related_resolution_ticket_id"],
        "related_resolution_phase06_at": record["related_resolution_phase06_at"],
        "explicit_linked_resolution_after_phase06": bool(record["explicit_linked_resolution_after_phase06"]),
        "production_exposure_status": record["production_exposure_status"],
        "risk_score": record["risk_score"],
        "risk_tier": record["risk_tier"],
        "risk_flags": record["risk_flags"],
        "risk_basis": record["risk_basis"],
        "automated_analysis": record["automated_analysis"],
        "evidence_index": record["evidence_index"],
        "comment_count": record["comment_evidence"]["comment_count"],
        "audit_history_event_count": record["audit_history_event_count"],
        "description_excerpt": record["description_excerpt"],
        "plan_evidence_excerpt": record["plan_evidence_excerpt"],
        "decision_evidence_excerpt": record["decision_evidence_excerpt"],
        "no_fix_evidence_excerpt": record["no_fix_evidence_excerpt"],
    }
    if include_full_evidence:
        public_record["description_full"] = record["description"]
        public_record["comment_records"] = [
            dict(item) for item in record["comment_evidence"]["comment_records"]
        ]
        public_record["audit_history_events"] = [
            dict(item) for item in record["audit_history_events"]
        ]
    return public_record


def _risk_tier_rows_phase06(records: list[dict[str, Any]]) -> list[dict[str, object]]:
    order = (
        "Field-escape risk flagged",
        "Root-cause quality concern",
        "Resolution evidence incomplete",
        "Resolution with residual risk",
        "Effective resolution (verified)",
    )
    counts = Counter(record["risk_tier"] for record in records)
    return [{"label": label, "count": counts.get(label, 0)} for label in order]


def build_phase06_idcevo_report_payload(
    *,
    db_path: str | Path | None = None,
    include_china_specific: bool = False,
    include_full_evidence: bool = False,
) -> dict[str, object]:
    resolved_db_path = resolve_report_db_path(db_path)
    require_tables(resolved_db_path, ("octane_defects", "octane_defect_history_events"))
    population_start = "2025-01-01"
    population_end_exclusive = (
        datetime.now(timezone.utc).date() + timedelta(days=1)
    ).isoformat()

    connection = sqlite3.connect(str(resolved_db_path))
    connection.row_factory = sqlite3.Row
    try:
        defect_columns = _table_columns(connection, "octane_defects")
        history_columns = _table_columns(connection, "octane_defect_history_events")
        required_phase = "phase" in defect_columns
        project_scope_field = "tproject" if "tproject" in defect_columns else "project"
        required_project = project_scope_field in defect_columns
        if not required_phase or not required_project:
            raise ValueError("octane_defects must contain phase and tproject or project columns")

        optional_columns = {
            name: _optional_column(defect_columns, name)
            for name in (
                "name",
                "description",
                "comments",
                "creation_time",
                "last_modified",
                "year",
                "project",
                "tproject",
                "team",
                "problem_finder_team",
                "solution_cluster",
                "defect_category",
                "product_areas",
                "function2modul",
                "function_responsible",
                "assigned_ecu",
                "ecu_to_modul",
                "software_version",
                "blocking_reason",
                "reprel_changes",
                "severity",
                "problem_severity",
                "reporting_class",
                "parent_child_type",
                "relation_to",
                "fetched_at",
            )
        }
        defect_rows = connection.execute(
            """
            SELECT defect_id,
                     phase AS phase,
                   {name} AS name,
                   {description} AS description,
                   {comments} AS comments,
                   {creation_time} AS creation_time,
                   {last_modified} AS last_modified,
                   {year} AS year,
                   {project} AS legacy_project,
                   {tproject} AS final_project,
                   {team} AS team,
                   {problem_finder_team} AS problem_finder_team,
                   {solution_cluster} AS solution_cluster,
                   {defect_category} AS defect_category,
                   {product_areas} AS product_areas,
                   {function2modul} AS function2modul,
                   {function_responsible} AS function_responsible,
                   {assigned_ecu} AS assigned_ecu,
                   {ecu_to_modul} AS ecu_to_modul,
                   {software_version} AS software_version,
                   {blocking_reason} AS blocking_reason,
                   {reprel_changes} AS reprel_changes,
                   {severity} AS severity,
                   {problem_severity} AS problem_severity,
                   {reporting_class} AS reporting_class,
                   {parent_child_type} AS parent_child_type,
                   {relation_to} AS relation_to,
                   {fetched_at} AS fetched_at
            FROM octane_defects
            WHERE upper(trim(coalesce({project_scope}, ''))) = 'IDCEVO'
                            AND creation_time >= ?
                            AND creation_time < ?
            ORDER BY defect_id
                        """.format(
                            **optional_columns,
                            project_scope=_optional_column(
                                defect_columns, project_scope_field
                            ),
                        ),
                        (population_start, population_end_exclusive),
        ).fetchall()

        scoped_records: dict[str, dict[str, Any]] = {}
        idcevo_all_ticket_total = 0
        idcevo_phase06_total = 0
        china_excluded_total = 0
        china_specific_in_scope_total = 0
        coc_development_team_excluded_total = 0
        latest_fetched_at = ""
        latest_modified_at = ""
        for row in defect_rows:
            ticket_id = _text(row["defect_id"])
            if not ticket_id:
                continue
            idcevo_all_ticket_total += 1
            if _phase_code(row["phase"]) != "06":
                continue
            idcevo_phase06_total += 1
            solution_cluster = _text(row["solution_cluster"])
            defect_category = _text(row["defect_category"])
            china_specific_ticket = is_china_specific(
                solution_cluster=solution_cluster,
                defect_category=defect_category,
            )
            if china_specific_ticket:
                if not include_china_specific:
                    china_excluded_total += 1
                    continue
            problem_finder_team = _text(row["problem_finder_team"])
            source_team = _text(row["team"])
            report_team = problem_finder_team or source_team
            if _is_coc_development_ticket(
                problem_finder_team=problem_finder_team,
            ):
                coc_development_team_excluded_total += 1
                continue
            if china_specific_ticket:
                china_specific_in_scope_total += 1
            fetched_at = _text(row["fetched_at"])
            last_modified = _text(row["last_modified"])
            latest_fetched_at = max(latest_fetched_at, fetched_at)
            latest_modified_at = max(latest_modified_at, last_modified)
            scoped_records[ticket_id] = {
                "ticket_id": ticket_id,
                "name": _text(row["name"]),
                "description": _text(row["description"]),
                "creation_time": _text(row["creation_time"]),
                "last_modified": last_modified,
                "year": _year_from_record(row["year"], row["creation_time"]),
                "legacy_project": _text(row["legacy_project"]),
                "final_project": _text(row["final_project"]),
                "team": report_team,
                "solution_cluster": solution_cluster,
                "defect_category": defect_category,
                "product_areas": _text(row["product_areas"]),
                "function2modul": _text(row["function2modul"]),
                "function_responsible": _text(row["function_responsible"]),
                "assigned_ecu": _text(row["assigned_ecu"]),
                "ecu_to_modul": _text(row["ecu_to_modul"]),
                "software_version": _text(row["software_version"]),
                "blocking_reason": _text(row["blocking_reason"]),
                "reprel_changes": _text(row["reprel_changes"]),
                "severity": _text(row["severity"]),
                "problem_severity": _text(row["problem_severity"]),
                "reporting_class": _text(row["reporting_class"]),
                "parent_child_type": _text(row["parent_child_type"]),
                "relation_to": _text(row["relation_to"]),
                "comment_evidence": _comment_evidence(
                    row["comments"],
                    capture_comment_records=include_full_evidence,
                ),
                "direct_close": False,
                "seen_phase06": True,
                "latest_phase06_at": "",
                "reopened_after_phase06": False,
                "phase_history_count": 0,
                "audit_history_event_count": 0,
                "audit_history_events": [],
                "history_blocking_reason": "",
                "history_blocked_reason": "",
                "rejection_route": "",
                "target_version_events": [],
                "planning_window_events": [],
                "reprel_history_values": [],
                "postponement_document_events": [],
                "postponement_decision_date_events": [],
                "commercial_solution_date_events": [],
            }

        history_rows = _history_rows(
            connection,
            list(scoped_records),
            history_columns,
        )
        _append_history_evidence(
            scoped_records,
            history_rows,
            capture_audit_history=include_full_evidence,
            target_phase="06",
        )
        relation_targets = {
            target
            for record in scoped_records.values()
            if (target := _extract_relation_target(record["relation_to"]))
        }
        parents = _parent_rows(connection, relation_targets, defect_columns)
        parent_history_rows = _history_rows(connection, list(parents), history_columns)
        parent_phase06_at = _latest_phase_entry_timestamps(
            parent_history_rows,
            phase_code="06",
        )
        for parent_id, parent in parents.items():
            parent["phase06_at"] = parent_phase06_at.get(parent_id, "")
    finally:
        connection.close()

    for record in scoped_records.values():
        _assess_record_phase06(record, parents)

    records = sorted(
        scoped_records.values(),
        key=lambda record: (
            -int(record["risk_score"]),
            str(record["risk_tier"]),
            str(record["ticket_id"]),
        ),
    )
    public_records = [
        _public_record_phase06(record, include_full_evidence=include_full_evidence)
        for record in records
    ]
    risk_tier_rows = _risk_tier_rows_phase06(records)
    risk_counts = {str(row["label"]): int(row["count"]) for row in risk_tier_rows}
    management_decision_total = sum(
        1
        for record in records
        if "management decision" in _normalized(record["effective_reason"])
    )
    management_decision_records = [
        record
        for record in records
        if "management decision" in _normalized(record["effective_reason"])
    ]
    history_reason_total = sum(
        1
        for record in records
        if record["reason_source"] in {"Phase-history blocking reason", "Phase-history blocked reason"}
    )
    closure_evidence = {
        "direct_close_total": sum(
            1
            for record in records
            if record["direct_close"]
        ),
        "reopened_after_phase06_total": sum(
            1
            for record in records
            if record["reopened_after_phase06"]
        ),
        "resolution_evidence_total": sum(
            1
            for record in records
            if record["repair_plan_status"] != "Resolution evidence incomplete"
        ),
        "target_version_after_phase06_total": sum(
            1
            for record in records
            if record["target_version_status"] == "Target version recorded after current Phase 06"
        ),
        "target_version_before_phase06_total": sum(
            1
            for record in records
            if record["target_version_status"] == "Target version recorded before current Phase 06"
        ),
        "planning_window_after_phase06_total": sum(
            1
            for record in records
            if record["planning_window_status"] == "Planning window recorded after current Phase 06"
        ),
        "planning_window_before_phase06_total": sum(
            1
            for record in records
            if record["planning_window_status"] == "Planning window recorded before current Phase 06"
        ),
        "reprel_changes_positive_total": sum(
            1
            for record in records
            if isinstance(record["reprel_change_count"], int)
            and int(record["reprel_change_count"]) > 0
        ),
        "reprel_changes_zero_total": sum(
            1
            for record in records
            if record["reprel_change_count"] == 0
        ),
        "reprel_change_count_missing_total": sum(
            1
            for record in records
            if record["reprel_change_count"] is None
        ),
        "explicit_linked_resolution_after_phase06_total": sum(
            1
            for record in records
            if record["explicit_linked_resolution_after_phase06"]
        ),
        "no_target_shift_or_explicit_resolution_total": sum(
            1
            for record in records
            if record["target_version_status"] == "No target version recorded"
            and record["planning_window_status"] == "No planning window recorded"
            and record["reprel_change_count"] in {None, 0}
            and record["repair_plan_status"] == "Resolution evidence incomplete"
            and not record["explicit_linked_resolution_after_phase06"]
        ),
        "no_remediation_or_linked_resolution_evidence_total": sum(
            1
            for record in records
            if not record["has_remediation_or_resolution_evidence"]
        ),
        "resolved_with_verified_fix_total": sum(
            1
            for record in records
            if record["production_exposure_status"] == "Resolved with verified fix"
        ),
        "resolved_but_field_escape_risk_remains_total": sum(
            1
            for record in records
            if record["production_exposure_status"] == "Resolved but field-escape risk remains"
        ),
        "resolved_without_root_cause_verification_total": sum(
            1
            for record in records
            if record["production_exposure_status"] == "Resolved without root-cause verification"
        ),
    }

    return {
        "generated_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "source": {
            "db_path": str(resolved_db_path),
            "latest_fetched_at": latest_fetched_at,
            "latest_last_modified": latest_modified_at,
            "population_start": population_start,
            "population_end_exclusive": population_end_exclusive,
            "project_scope_field": project_scope_field,
            "project_scope_note": (
                "Final business project (tproject) is used for scope; legacy project is retained for audit."
                if project_scope_field == "tproject"
                else "Legacy project is used because this source database has no tproject column."
            ),
            "report_title": (
                "IDCEVO Phase 06 Resolution Quality Review"
                if include_china_specific
                else "IDCEVO Phase 06 Global Resolution Quality Review"
            ),
            "scope_label": (
                "Analyzed IDCEVO Phase 06"
                if include_china_specific
                else "Analyzed Global Phase 06"
            ),
            "scope_note": (
                "China-specific included: {} | CoC finder teams: {} excluded".format(
                    china_specific_in_scope_total,
                    coc_development_team_excluded_total,
                )
                if include_china_specific
                else "China-specific: {} | CoC finder teams: {} excluded".format(
                    china_excluded_total,
                    coc_development_team_excluded_total,
                )
            ),
            "scope_description": (
                "All IDCEVO tickets created from 2025-01-01 through the source snapshot are counted first. "
                "IDCEVO scope uses the final business project tproject. "
                "This analysis then selects their current Phase 06 (concluded) tickets, includes China-specific functions and "
                "excludes tickets whose Problem Finder Team contains CoC."
                if include_china_specific
                else "All IDCEVO tickets created from 2025-01-01 through the source snapshot are counted first. "
                "IDCEVO scope uses the final business project tproject. "
                "This analysis then selects their current Phase 06 (concluded) tickets, excludes China-specific functions and "
                "tickets raised by CoC development teams."
            ),
            "scope_rule": (
                "Use final business project tproject for IDCEVO scope when available; use legacy project only when tproject is unavailable. "
                "Count all scoped IDCEVO tickets in the creation-time window first, then select tickets whose current phase is 06. "
                + (
                    "Include China-specific functions. "
                    if include_china_specific
                    else "Exclude China-specific functions when Solution Cluster is China-specific; use Defect Category only when Solution Cluster is blank. "
                )
                + "Exclude tickets when Problem Finder Team contains CoC."
            ),
        },
        "summary": {
            "idcevo_all_ticket_total": idcevo_all_ticket_total,
            "idcevo_current_phase06_total": idcevo_phase06_total,
            "idcevo_phase06_total": idcevo_phase06_total,
            "china_excluded_total": china_excluded_total,
            "china_specific_in_scope_total": china_specific_in_scope_total,
            "coc_development_team_excluded_total": coc_development_team_excluded_total,
            "analyzed_phase06_total": len(records),
            "global_phase06_total": len(records),
            "history_coverage_total": sum(1 for record in records if record["phase_history_count"] > 0),
            "direct_close_total": sum(1 for record in records if record["direct_close"]),
            "governed_severe_total": sum(
                1 for record in records if record["is_governed_severe"]
            ),
            "reopened_after_phase06_total": sum(1 for record in records if record["reopened_after_phase06"]),
            "history_reason_total": history_reason_total,
            "management_decision_total": management_decision_total,
            "effective_resolution_total": risk_counts.get("Effective resolution (verified)", 0),
            "resolution_with_residual_risk_total": risk_counts.get("Resolution with residual risk", 0),
            "resolution_evidence_incomplete_total": risk_counts.get("Resolution evidence incomplete", 0),
            "root_cause_quality_concern_total": risk_counts.get("Root-cause quality concern", 0),
            "field_escape_risk_flagged_total": risk_counts.get("Field-escape risk flagged", 0),
        },
        "risk_tiers": risk_tier_rows,
        "distributions": {
            "years": _counter_rows(records, "year", limit=10),
            "solution_clusters": _counter_rows(records, "solution_cluster", limit=40),
            "defect_categories": _counter_rows(records, "defect_category", limit=40),
            "teams": _counter_rows(records, "team", limit=40),
            "effective_reasons": _counter_rows(records, "effective_reason", limit=40),
            "repair_plan_statuses": _counter_rows(records, "repair_plan_status", limit=20),
            "severity": _counter_rows(records, "severity", limit=20),
            "problem_severity": _counter_rows(records, "problem_severity", limit=20),
            "reporting_classes": _counter_rows(records, "reporting_class", limit=30),
            "blocking_reason_groups": _counter_rows(records, "blocking_reason_group", limit=20),
            "closure_dispositions": _counter_rows(records, "closure_disposition", limit=20),
            "target_version_statuses": _counter_rows(records, "target_version_status", limit=10),
            "commercial_solution_date_statuses": _counter_rows(
                records, "commercial_solution_date_status", limit=10
            ),
            "postponement_document_statuses": _counter_rows(
                records, "postponement_document_status", limit=10
            ),
            "follow_up_statuses": _counter_rows(records, "follow_up_status", limit=10),
            "reprel_change_statuses": _counter_rows(records, "reprel_change_status", limit=30),
            "related_resolution_statuses": _counter_rows(records, "related_resolution_status", limit=20),
            "production_exposure_statuses": _counter_rows(records, "production_exposure_status", limit=20),
        },
        "closure_evidence": closure_evidence,
        "management_decision": {
            "total": management_decision_total,
            "field_escape_risk_flagged_total": sum(
                1
                for record in management_decision_records
                if record["risk_tier"] == "Field-escape risk flagged"
            ),
            "root_cause_quality_concern_total": sum(
                1
                for record in management_decision_records
                if record["risk_tier"] == "Root-cause quality concern"
            ),
            "no_repair_evidence_total": sum(
                1
                for record in management_decision_records
                if record["repair_plan_status"] == "Resolution evidence incomplete"
            ),
            "reopened_after_phase06_total": sum(
                1
                for record in management_decision_records
                if record["reopened_after_phase06"]
            ),
            "risk_tiers": _risk_tier_rows_phase06(management_decision_records),
        },
        "tickets": public_records,
    }