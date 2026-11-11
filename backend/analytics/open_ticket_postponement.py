from __future__ import annotations

from collections import Counter
import csv
from hashlib import sha256
from html import unescape as html_unescape
import json
from pathlib import Path
import re
import sqlite3
from typing import Mapping, Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


RELEASE_PATTERN = re.compile(
    r"(?<!\d)(?P<year>2[4-9])[-_/ ]?(?P<month>03|07|11)(?!\d)",
    re.IGNORECASE,
)
RELEASE_MONTH_INDEX = {3: 0, 7: 1, 11: 2}
INPUT_VERSION = "open-shift-ticket-plan-llm-v3"
TARGET_I_STEP_FIELD = "target_i_step_udf"
LEGACY_TARGET_I_STEP_FIELD = "legacy_target_i_step_udf"
REQUESTED_TARGET_I_STEP_FIELD = "req_target_istep_udf"
REQUESTED_TARGET_RELEASE_FIELD = "requested_target_i_step_release_udf"
TARGET_WEEK_FIELD = "target_week_udf"
TARGET_SET_FIELD = "target_set_udf"
FIRST_USE_SOP_FIELD = "first_use_sop_of_function_udf"
COMPLETED_IN_VERSION_FIELD = "closed_in_ver_udf"
SW_HRL_CLOSED_VERSION_FIELD = "sw_hrl_closed_version_udf"
APP_VERSION_FIELD = "app_version_udf"
SUPPLIER_CONFIRMED_I_STEP_FIELD = "supplier_confirmed_i_step_udf"
PREDICTED_TARGET_I_STEP_FIELD = "predicted_target_i_step_udf"
SUPPLIER_CONFIRMED_TARGET_WEEK_FIELD = "supplier_confirmed_target_week_udf"
COMMERCIAL_SOLUTION_DATE_FIELD = "com_sol_avail_date_udf"
POSTPONEMENT_DOCUMENT_FIELD = "postpone_tolerate_doc_udf"
POSTPONEMENT_DECISION_DATE_FIELD = "postpone_toleratedate_udf"
REPREL_CHANGES_FIELD = "reprel_changes_udf"
SOLUTION_RESPONSIBLE_FIELD = "solution_responsible_udf"
NON_CONCRETE_TARGET_VALUES = frozenset(
    {
        "",
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
GENERIC_CLOSED_VERSION_VALUES = NON_CONCRETE_TARGET_VALUES | frozenset(
    {
        "backendfix",
        "application modules",
    }
)
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
WHITESPACE_PATTERN = re.compile(r"\s+")
EVIDENCE_FIELD_NAMES = frozenset(
    {
        TARGET_I_STEP_FIELD,
        LEGACY_TARGET_I_STEP_FIELD,
        REQUESTED_TARGET_I_STEP_FIELD,
        REQUESTED_TARGET_RELEASE_FIELD,
        TARGET_WEEK_FIELD,
        TARGET_SET_FIELD,
        FIRST_USE_SOP_FIELD,
        COMPLETED_IN_VERSION_FIELD,
        SW_HRL_CLOSED_VERSION_FIELD,
        APP_VERSION_FIELD,
        SUPPLIER_CONFIRMED_I_STEP_FIELD,
        PREDICTED_TARGET_I_STEP_FIELD,
        SUPPLIER_CONFIRMED_TARGET_WEEK_FIELD,
        COMMERCIAL_SOLUTION_DATE_FIELD,
        POSTPONEMENT_DOCUMENT_FIELD,
        POSTPONEMENT_DECISION_DATE_FIELD,
        REPREL_CHANGES_FIELD,
        SOLUTION_RESPONSIBLE_FIELD,
    }
)
REPORT_COLUMNS = (
    "Confidence Category",
    "Confidence Basis",
    "Ticket ID",
    "Title",
    "Phase",
    "Project",
    "Team",
    "Function Responsible",
    "Solution Responsible",
    "Creation Time",
    "Last Modified",
    "Source Software (full)",
    "Source PU/I-Step",
    "Target Software / I-Step (full)",
    "Target PU/I-Step",
    "Target Solution Evidence Field",
    "Target Recorded At",
    "Shift-to-Set PU",
    "First Use/SOP PU",
    "Supplier Confirmed I-Step",
    "Predicted Target I-Step",
    "Requested Target Release",
    "Target Week",
    "Target Set",
    "Supplier Confirmed Target Week",
    "Commercial Solution Date",
    "Postponement Decision Date",
    "Reprel Changes",
    "Closed in Version",
    "SW-HRL Closed Version",
    "App Version",
    "Phase 07 Fix Version Evidence",
    "Phase 07 Fix Version Quality",
    "Plan Evidence Signals",
    "Target Change Trail",
    "Postponement Evidence Excerpt",
    "Classification",
    "Postponed Release Steps",
    "Version Comparison Note",
    "LLM Summary Source",
    "LLM Issue Summary",
    "LLM Postponement Assessment",
    "LLM Review Focus",
)
SHIFT_CONFIDENCE_CATEGORY_ORDER = (
    "Confirmed PU Shift",
    "Phase 07 Concrete Fix-Version Plan",
    "Requested Target Review",
    "Supplier-Confirmed Future Target",
    "Predicted Future Target",
    "First Use/SOP Future Plan",
    "Phase 07 Closure Evidence - Verify",
    "Target Data Check",
)
SHIFT_CONFIDENCE_CATEGORY_DESCRIPTIONS = {
    "Confirmed PU Shift": "Target I-Step or Shift-to-Set PU is strictly later than reported software PU.",
    "Phase 07 Concrete Fix-Version Plan": "Phase 07 has a concrete Closed in version, SW-HRL, or App version recorded.",
    "Requested Target Review": "Requested Target I-Step is later but no stronger confirmed shift is recorded.",
    "Supplier-Confirmed Future Target": "Supplier-confirmed I-Step is later but no stronger shift evidence is recorded.",
    "Predicted Future Target": "Predicted Target I-Step is later but no stronger shift evidence is recorded.",
    "First Use/SOP Future Plan": "First Use/SOP PU is later; it is future applicability evidence, not delivery proof.",
    "Phase 07 Closure Evidence - Verify": "Phase 07 records a generic closure label; verify the concrete software version.",
    "Target Data Check": "Target evidence is equal, earlier, or not comparable to reported software PU.",
}
CLASSIFICATION_LABELS = {
    "confirmed_postponed": "Confirmed postponed: Target I-Step is later than reported software",
    "confirmed_shift_to_set": "Confirmed shift: Shift-to-Set PU is later than reported software",
    "phase07_concrete_fix_version_plan": "Phase 07 plan: concrete future fix version is recorded",
    "first_use_sop_future_plan": "Future plan: First Use/SOP PU is later than reported software",
    "requested_target_review": "Review only: requested target is later but Target I-Step is not confirmed",
    "same_target_not_postponed": "Not postponed: Target I-Step equals reported software release",
    "earlier_target_data_check": "Data check: Target I-Step precedes reported software release",
    "needs_manual_version_comparison": "Manual version comparison required: source or target release is not standard",
    "legacy_target_review": "Review only: legacy target field is later than reported software",
    "no_concrete_target": "No concrete target I-Step recorded",
}
SHEET_CLASSIFICATIONS = (
    ("01_Confirmed_Postponed", "confirmed_postponed"),
    ("02_Requested_Target_Review", "requested_target_review"),
    ("03_Same_Target_Not_Postponed", "same_target_not_postponed"),
    ("04_Earlier_Target_Data_Check", "earlier_target_data_check"),
    ("05_Manual_Version_Comparison", "needs_manual_version_comparison"),
)


def _text(value: object) -> str:
    return str(value or "").strip()


def clean_text(value: object) -> str:
    without_tags = HTML_TAG_PATTERN.sub(" ", _text(value))
    return WHITESPACE_PATTERN.sub(" ", html_unescape(without_tags)).strip()


def bounded_text(value: object, maximum: int) -> str:
    text = clean_text(value)
    if len(text) <= maximum:
        return text
    head_length = int(maximum * 0.75)
    tail_length = maximum - head_length
    return "{} [source truncated] {}".format(
        text[:head_length].rstrip(),
        text[-tail_length:].lstrip(),
    )


def _history_value(event: Mapping[str, object], prefix: str) -> str:
    return _text(event.get(f"{prefix}_value_text")) or _text(event.get(f"{prefix}_value"))


def _history_sort_key(event: Mapping[str, object]) -> tuple[str, int, int]:
    def index_value(value: object) -> int:
        try:
            return int(value or -1)
        except (TypeError, ValueError):
            return -1

    return (
        _text(event.get("event_timestamp")),
        index_value(event.get("entry_index")),
        index_value(event.get("change_index")),
    )


def _release_label(release: tuple[int, int] | None) -> str:
    return "{}-{:02d}".format(release[0], release[1]) if release else ""


def _is_concrete_target(value: object) -> bool:
    return _text(value).casefold() not in NON_CONCRETE_TARGET_VALUES


def _is_concrete_closed_version(value: object) -> bool:
    return _text(value).casefold() not in GENERIC_CLOSED_VERSION_VALUES


def _comments_text(value: object) -> str:
    raw = _text(value)
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return clean_text(raw)
    if not isinstance(parsed, list):
        return clean_text(parsed)
    entries: list[str] = []
    for item in parsed[-8:]:
        if isinstance(item, Mapping):
            text = clean_text(item.get("text") or item.get("content") or item.get("comment"))
        else:
            text = clean_text(item)
        if text:
            entries.append(text)
    return "\n".join(entries)


def parse_release(value: object) -> tuple[int, int] | None:
    match = RELEASE_PATTERN.search(str(value or ""))
    if not match:
        return None
    return int(match.group("year")), int(match.group("month"))


def release_index(release: tuple[int, int]) -> int:
    return release[0] * len(RELEASE_MONTH_INDEX) + RELEASE_MONTH_INDEX[release[1]]


def classify_ticket(*, software_version: str, target_i_step: str) -> dict[str, object]:
    source_release = parse_release(software_version)
    target_release = parse_release(target_i_step)
    if source_release is None or target_release is None:
        return {
            "classification": "needs_manual_version_comparison",
            "postponed_release_steps": None,
        }

    release_delta = release_index(target_release) - release_index(source_release)
    if release_delta > 0:
        classification = "confirmed_postponed"
    elif release_delta == 0:
        classification = "same_target_not_postponed"
    else:
        classification = "earlier_target_data_check"
    return {
        "classification": classification,
        "postponed_release_steps": release_delta,
    }


def _has_later_release(*, software_version: str, target_version: str) -> bool:
    return (
        classify_ticket(
            software_version=software_version,
            target_i_step=target_version,
        )["classification"]
        == "confirmed_postponed"
    )


def build_ticket_record(
    ticket: Mapping[str, object],
    history_events: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    ordered_events = sorted(history_events, key=_history_sort_key)
    latest_by_field: dict[str, tuple[str, str]] = {}
    target_change_lines: list[str] = []
    evidence_lines: list[str] = []
    for event in ordered_events:
        field_name = _text(event.get("field_name"))
        if not field_name:
            continue
        old_value = clean_text(_history_value(event, "old"))
        new_value = clean_text(_history_value(event, "new"))
        timestamp = _text(event.get("event_timestamp"))
        latest_by_field[field_name] = (new_value, timestamp)
        if field_name in {TARGET_I_STEP_FIELD, TARGET_SET_FIELD} and (old_value or new_value):
            target_change_lines.append(
                "{} | {} | {} -> {}".format(
                    timestamp or "Not recorded",
                    field_name,
                    old_value or "(blank)",
                    new_value or "(blank)",
                )
            )
        if field_name in EVIDENCE_FIELD_NAMES and (old_value or new_value):
            evidence_lines.append(
                "{} | {}: {} -> {}".format(
                    timestamp or "Not recorded",
                    field_name,
                    old_value or "(blank)",
                    new_value or "(blank)",
                )
            )

    def latest_value(field_name: str) -> str:
        return latest_by_field.get(field_name, ("", ""))[0]

    def latest_at(field_name: str) -> str:
        return latest_by_field.get(field_name, ("", ""))[1]

    software_version = _text(ticket.get("software_version"))
    direct_target = latest_value(TARGET_I_STEP_FIELD)
    legacy_target = latest_value(LEGACY_TARGET_I_STEP_FIELD)
    shift_to_set = latest_value(TARGET_SET_FIELD)
    first_use_sop = latest_value(FIRST_USE_SOP_FIELD) or _text(
        ticket.get("first_use_sop_of_function")
    )
    closed_in_version = latest_value(COMPLETED_IN_VERSION_FIELD)
    sw_hrl_closed_version = latest_value(SW_HRL_CLOSED_VERSION_FIELD)
    app_version = latest_value(APP_VERSION_FIELD)
    supplier_confirmed_i_step = latest_value(SUPPLIER_CONFIRMED_I_STEP_FIELD)
    predicted_target_i_step = latest_value(PREDICTED_TARGET_I_STEP_FIELD)
    phase07_fix_version_entries = [
        "{}: {}".format(label, value)
        for label, value in (
            ("Closed in version", closed_in_version),
            ("SW-HRL closed version", sw_hrl_closed_version),
            ("App version", app_version),
        )
        if _text(value)
    ]
    concrete_phase07_fix_versions = [
        entry
        for label, value in (
            ("Closed in version", closed_in_version),
            ("SW-HRL closed version", sw_hrl_closed_version),
            ("App version", app_version),
        )
        if _is_concrete_closed_version(value)
        for entry in ("{}: {}".format(label, value),)
    ]
    phase_is_07 = _text(ticket.get("phase")).startswith("07")
    if not phase_is_07:
        phase07_fix_version_quality = "Not a Phase 07 ticket"
    elif concrete_phase07_fix_versions:
        phase07_fix_version_quality = "Concrete future fix version recorded"
    elif phase07_fix_version_entries:
        phase07_fix_version_quality = "Generic closure label only; no concrete software version"
    else:
        phase07_fix_version_quality = "No closed-version evidence recorded"
    requested_target = latest_value(REQUESTED_TARGET_I_STEP_FIELD)
    has_later_target_i_step = _has_later_release(
        software_version=software_version,
        target_version=direct_target,
    )
    has_later_shift_to_set = _has_later_release(
        software_version=software_version,
        target_version=shift_to_set,
    )
    has_later_first_use_sop = _has_later_release(
        software_version=software_version,
        target_version=first_use_sop,
    )
    has_later_requested_target = _has_later_release(
        software_version=software_version,
        target_version=requested_target,
    )
    has_later_supplier_confirmed_i_step = _has_later_release(
        software_version=software_version,
        target_version=supplier_confirmed_i_step,
    )
    has_later_predicted_target_i_step = _has_later_release(
        software_version=software_version,
        target_version=predicted_target_i_step,
    )
    plan_evidence_signals = [
        "{}: {}".format(label, value)
        for label, value in (
            ("Target I-Step", direct_target),
            ("Shift-to-Set PU", shift_to_set),
            ("First Use/SOP PU", first_use_sop),
            ("Closed in version", closed_in_version),
            ("SW-HRL closed version", sw_hrl_closed_version),
            ("App version", app_version),
            ("Supplier confirmed I-Step", supplier_confirmed_i_step),
            ("Predicted target I-Step", predicted_target_i_step),
            ("Requested target I-Step", requested_target),
        )
        if _text(value)
    ]
    if _is_concrete_target(direct_target):
        target_i_step = direct_target
        target_evidence_field = TARGET_I_STEP_FIELD
        target_recorded_at = latest_at(TARGET_I_STEP_FIELD)
        comparison = classify_ticket(
            software_version=software_version,
            target_i_step=target_i_step,
        )
    elif _is_concrete_target(legacy_target):
        target_i_step = legacy_target
        target_evidence_field = LEGACY_TARGET_I_STEP_FIELD
        target_recorded_at = latest_at(LEGACY_TARGET_I_STEP_FIELD)
        comparison = classify_ticket(
            software_version=software_version,
            target_i_step=target_i_step,
        )
        if comparison["classification"] == "confirmed_postponed":
            comparison["classification"] = "legacy_target_review"
        else:
            comparison = {
                "classification": "no_concrete_target",
                "postponed_release_steps": None,
            }
    elif _is_concrete_target(shift_to_set):
        target_i_step = ""
        target_evidence_field = TARGET_SET_FIELD
        target_recorded_at = latest_at(TARGET_SET_FIELD)
        comparison = classify_ticket(
            software_version=software_version,
            target_i_step=shift_to_set,
        )
        if comparison["classification"] == "confirmed_postponed":
            comparison["classification"] = "confirmed_shift_to_set"
        else:
            comparison = {
                "classification": "no_concrete_target",
                "postponed_release_steps": None,
            }
    elif phase_is_07 and concrete_phase07_fix_versions:
        target_i_step = ""
        target_evidence_field = COMPLETED_IN_VERSION_FIELD
        target_recorded_at = latest_at(COMPLETED_IN_VERSION_FIELD)
        comparison = {
            "classification": "phase07_concrete_fix_version_plan",
            "postponed_release_steps": None,
        }
    elif _is_concrete_target(requested_target):
        target_i_step = requested_target
        target_evidence_field = REQUESTED_TARGET_I_STEP_FIELD
        target_recorded_at = latest_at(REQUESTED_TARGET_I_STEP_FIELD)
        comparison = classify_ticket(
            software_version=software_version,
            target_i_step=target_i_step,
        )
        if comparison["classification"] == "confirmed_postponed":
            comparison["classification"] = "requested_target_review"
        else:
            comparison = {
                "classification": "no_concrete_target",
                "postponed_release_steps": None,
            }
    elif _is_concrete_target(first_use_sop):
        target_i_step = ""
        target_evidence_field = FIRST_USE_SOP_FIELD
        target_recorded_at = latest_at(FIRST_USE_SOP_FIELD)
        comparison = classify_ticket(
            software_version=software_version,
            target_i_step=first_use_sop,
        )
        if comparison["classification"] == "confirmed_postponed":
            comparison["classification"] = "first_use_sop_future_plan"
        else:
            comparison = {
                "classification": "no_concrete_target",
                "postponed_release_steps": None,
            }
    else:
        target_i_step = ""
        target_evidence_field = ""
        target_recorded_at = ""
        comparison = {
            "classification": "no_concrete_target",
            "postponed_release_steps": None,
        }

    source_release = parse_release(software_version)
    target_release = parse_release(target_i_step or shift_to_set or first_use_sop)
    return {
        "ticket_id": _text(ticket.get("ticket_id") or ticket.get("defect_id")),
        "name": _text(ticket.get("name")),
        "phase": _text(ticket.get("phase")),
        "project": _text(ticket.get("project") or ticket.get("tproject")),
        "team": _text(ticket.get("problem_finder_team") or ticket.get("team")),
        "function_responsible": _text(ticket.get("function_responsible")),
        "solution_responsible": latest_value(SOLUTION_RESPONSIBLE_FIELD)
        or _text(ticket.get("solution_responsible")),
        "creation_time": _text(ticket.get("creation_time")),
        "last_modified": _text(ticket.get("last_modified")),
        "software_version": software_version,
        "reported_release": _release_label(source_release),
        "target_i_step": target_i_step,
        "target_release": _release_label(target_release),
        "target_evidence_field": target_evidence_field,
        "target_recorded_at": target_recorded_at,
        "shift_to_set_pu": shift_to_set,
        "first_use_sop_pu": first_use_sop,
        "closed_in_version": closed_in_version,
        "sw_hrl_closed_version": sw_hrl_closed_version,
        "app_version": app_version,
        "supplier_confirmed_i_step": supplier_confirmed_i_step,
        "predicted_target_i_step": predicted_target_i_step,
        "phase07_fix_versions": "\n".join(phase07_fix_version_entries),
        "phase07_fix_version_quality": phase07_fix_version_quality,
        "has_later_target_i_step": has_later_target_i_step,
        "has_later_shift_to_set": has_later_shift_to_set,
        "has_later_first_use_sop": has_later_first_use_sop,
        "has_later_requested_target": has_later_requested_target,
        "has_later_supplier_confirmed_i_step": has_later_supplier_confirmed_i_step,
        "has_later_predicted_target_i_step": has_later_predicted_target_i_step,
        "has_concrete_phase07_fix_version": bool(concrete_phase07_fix_versions),
        "plan_evidence_signals": "\n".join(plan_evidence_signals),
        "requested_target_release": latest_value(REQUESTED_TARGET_RELEASE_FIELD),
        "target_week": latest_value(TARGET_WEEK_FIELD),
        "target_set": latest_value(TARGET_SET_FIELD),
        "supplier_confirmed_target_week": latest_value(
            SUPPLIER_CONFIRMED_TARGET_WEEK_FIELD
        ),
        "commercial_solution_date": latest_value(COMMERCIAL_SOLUTION_DATE_FIELD),
        "postponement_document": latest_value(POSTPONEMENT_DOCUMENT_FIELD),
        "postponement_decision_date": latest_value(POSTPONEMENT_DECISION_DATE_FIELD),
        "reprel_changes": latest_value(REPREL_CHANGES_FIELD),
        "target_change_trail": "\n".join(target_change_lines[-8:]),
        "history_evidence": "\n".join(evidence_lines[-20:]),
        "description": clean_text(ticket.get("description")),
        "comments": _comments_text(ticket.get("comments")),
        "classification": comparison["classification"],
        "postponed_release_steps": comparison["postponed_release_steps"],
    }


def build_llm_input_record(record: Mapping[str, object]) -> dict[str, object]:
    ticket_id = _text(record.get("ticket_id"))
    ticket = {
        "title": _text(record.get("name")),
        "phase": _text(record.get("phase")),
        "problem_evidence": bounded_text(record.get("description"), 4_500)
        or "No ticket description retained.",
        "comments_evidence": bounded_text(record.get("comments"), 4_500)
        or "No comment text retained.",
        "source_software_version": _text(record.get("software_version")),
        "source_release": _text(record.get("reported_release")),
        "target_i_step": _text(record.get("target_i_step")),
        "target_release": _text(record.get("target_release")),
        "target_evidence_field": _text(record.get("target_evidence_field")),
        "target_recorded_at": _text(record.get("target_recorded_at")),
        "shift_to_set_pu": _text(record.get("shift_to_set_pu")),
        "first_use_sop_pu": _text(record.get("first_use_sop_pu")),
        "supplier_confirmed_i_step": _text(record.get("supplier_confirmed_i_step")),
        "predicted_target_i_step": _text(record.get("predicted_target_i_step")),
        "target_change_trail": bounded_text(record.get("target_change_trail"), 1_600),
        "target_week": _text(record.get("target_week")),
        "target_set": _text(record.get("target_set")),
        "supplier_confirmed_target_week": _text(
            record.get("supplier_confirmed_target_week")
        ),
        "commercial_solution_date": _text(record.get("commercial_solution_date")),
        "postponement_decision_date": _text(record.get("postponement_decision_date")),
        "postponement_document_evidence": bounded_text(
            record.get("postponement_document"), 1_800
        )
        or "No postponement document retained.",
        "reprel_changes": _text(record.get("reprel_changes")),
        "closed_in_version": _text(record.get("closed_in_version")),
        "sw_hrl_closed_version": _text(record.get("sw_hrl_closed_version")),
        "app_version": _text(record.get("app_version")),
        "phase07_fix_versions": _text(record.get("phase07_fix_versions")),
        "phase07_fix_version_quality": _text(
            record.get("phase07_fix_version_quality")
        ),
        "plan_evidence_signals": bounded_text(record.get("plan_evidence_signals"), 2_000),
        "classification": _text(record.get("classification")),
        "postponed_release_steps": record.get("postponed_release_steps"),
        "history_evidence": bounded_text(record.get("history_evidence"), 3_500),
    }
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
    return {
        "ticket_id": ticket_id,
        "input_fingerprint": fingerprint,
        "input_version": INPUT_VERSION,
        "ticket": ticket,
    }


def _version_comparison_note(record: Mapping[str, object]) -> str:
    classification = _text(record.get("classification"))
    source_release = _text(record.get("reported_release"))
    target_release = _text(record.get("target_release"))
    if classification == "needs_manual_version_comparison":
        if not source_release and not target_release:
            return "Neither source nor target software contains a standard YY-03/07/11 release token."
        if not source_release:
            return "Source software does not contain a standard YY-03/07/11 release token."
        return "Target software does not contain a standard YY-03/07/11 release token."
    if source_release and target_release:
        return "Compared {} with {} using the 03 -> 07 -> 11 release cadence.".format(
            source_release,
            target_release,
        )
    return "No automatic release comparison is available."


def build_report_row(
    record: Mapping[str, object],
    llm_summary: Mapping[str, object] | None = None,
) -> dict[str, object]:
    summary = llm_summary or {}
    summary_source = _text(summary.get("source"))
    summary_model = _text(summary.get("model"))
    if summary_source == "llm":
        llm_source = "LLM / {}".format(summary_model or "configured model")
    else:
        llm_source = "Rules only / LLM not generated"
    classification = _text(record.get("classification"))
    confidence_category, confidence_basis = shift_confidence_category(record)
    return {
        "Confidence Category": confidence_category,
        "Confidence Basis": confidence_basis,
        "Ticket ID": _text(record.get("ticket_id")),
        "Title": _text(record.get("name")),
        "Phase": _text(record.get("phase")),
        "Project": _text(record.get("project")),
        "Team": _text(record.get("team")),
        "Function Responsible": _text(record.get("function_responsible")),
        "Solution Responsible": _text(record.get("solution_responsible")),
        "Creation Time": _text(record.get("creation_time")),
        "Last Modified": _text(record.get("last_modified")),
        "Source Software (full)": _text(record.get("software_version")),
        "Source PU/I-Step": _text(record.get("reported_release")),
        "Target Software / I-Step (full)": _text(record.get("target_i_step")),
        "Target PU/I-Step": _text(record.get("target_release")),
        "Target Solution Evidence Field": _text(record.get("target_evidence_field")),
        "Target Recorded At": _text(record.get("target_recorded_at")),
        "Shift-to-Set PU": _text(record.get("shift_to_set_pu")),
        "First Use/SOP PU": _text(record.get("first_use_sop_pu")),
        "Supplier Confirmed I-Step": _text(record.get("supplier_confirmed_i_step")),
        "Predicted Target I-Step": _text(record.get("predicted_target_i_step")),
        "Requested Target Release": _text(record.get("requested_target_release")),
        "Target Week": _text(record.get("target_week")),
        "Target Set": _text(record.get("target_set")),
        "Supplier Confirmed Target Week": _text(
            record.get("supplier_confirmed_target_week")
        ),
        "Commercial Solution Date": _text(record.get("commercial_solution_date")),
        "Postponement Decision Date": _text(record.get("postponement_decision_date")),
        "Reprel Changes": _text(record.get("reprel_changes")),
        "Closed in Version": _text(record.get("closed_in_version")),
        "SW-HRL Closed Version": _text(record.get("sw_hrl_closed_version")),
        "App Version": _text(record.get("app_version")),
        "Phase 07 Fix Version Evidence": _text(record.get("phase07_fix_versions")),
        "Phase 07 Fix Version Quality": _text(
            record.get("phase07_fix_version_quality")
        ),
        "Plan Evidence Signals": _text(record.get("plan_evidence_signals")),
        "Target Change Trail": _text(record.get("target_change_trail")),
        "Postponement Evidence Excerpt": bounded_text(
            record.get("postponement_document"), 1_200
        ),
        "Classification": CLASSIFICATION_LABELS.get(classification, classification),
        "Postponed Release Steps": record.get("postponed_release_steps") or "",
        "Version Comparison Note": _version_comparison_note(record),
        "LLM Summary Source": llm_source,
        "LLM Issue Summary": _text(summary.get("issue_summary")),
        "LLM Postponement Assessment": _text(summary.get("postponement_assessment")),
        "LLM Review Focus": _text(summary.get("review_focus")),
    }


def _optional_column(columns: set[str], name: str) -> str:
    return name if name in columns else "''"


def load_open_ticket_records(db_path: str | Path) -> list[dict[str, object]]:
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    try:
        defect_columns = {
            _text(row[1])
            for row in connection.execute("PRAGMA table_info(octane_defects)")
        }
        if "defect_id" not in defect_columns or "phase" not in defect_columns:
            raise ValueError("octane_defects must contain defect_id and phase columns")
        history_columns = {
            _text(row[1])
            for row in connection.execute("PRAGMA table_info(octane_defect_history_events)")
        }
        if "defect_id" not in history_columns or "field_name" not in history_columns:
            raise ValueError(
                "octane_defect_history_events must contain defect_id and field_name columns"
            )

        def defect_column(name: str) -> str:
            return _optional_column(defect_columns, name)

        def history_column(name: str) -> str:
            return _optional_column(history_columns, name)

        rows = connection.execute(
            """
            SELECT defect_id,
                   {name} AS name,
                   phase,
                   {project} AS project,
                   {tproject} AS tproject,
                   {team} AS team,
                   {problem_finder_team} AS problem_finder_team,
                   {function_responsible} AS function_responsible,
                   {solution_responsible} AS solution_responsible,
                   {creation_time} AS creation_time,
                   {last_modified} AS last_modified,
                   {software_version} AS software_version,
                   {description} AS description,
                   {comments} AS comments
            FROM octane_defects
            WHERE substr(trim(coalesce(phase, '')), 1, 2) NOT IN ('06', '09')
            ORDER BY defect_id
            """.format(
                name=defect_column("name"),
                project=defect_column("project"),
                tproject=defect_column("tproject"),
                team=defect_column("team"),
                problem_finder_team=defect_column("problem_finder_team"),
                function_responsible=defect_column("function_responsible"),
                solution_responsible=defect_column("solution_responsible"),
                creation_time=defect_column("creation_time"),
                last_modified=defect_column("last_modified"),
                software_version=defect_column("software_version"),
                description=defect_column("description"),
                comments=defect_column("comments"),
            )
        ).fetchall()
        history_rows = connection.execute(
            """
            SELECT history.defect_id,
                   history.field_name,
                   {old_value} AS old_value,
                   {new_value} AS new_value,
                   {old_value_text} AS old_value_text,
                   {new_value_text} AS new_value_text,
                   {event_timestamp} AS event_timestamp,
                   {entry_index} AS entry_index,
                   {change_index} AS change_index
            FROM octane_defect_history_events AS history
            INNER JOIN octane_defects AS defect
                ON defect.defect_id = history.defect_id
            WHERE substr(trim(coalesce(defect.phase, '')), 1, 2) NOT IN ('06', '09')
              AND history.field_name IN ({fields})
            ORDER BY history.defect_id,
                     history.event_timestamp,
                     history.entry_index,
                     history.change_index
            """.format(
                old_value="history." + history_column("old_value"),
                new_value="history." + history_column("new_value"),
                old_value_text="history." + history_column("old_value_text"),
                new_value_text="history." + history_column("new_value_text"),
                event_timestamp="history." + history_column("event_timestamp"),
                entry_index="history." + history_column("entry_index"),
                change_index="history." + history_column("change_index"),
                fields=",".join("?" for _ in EVIDENCE_FIELD_NAMES),
            ),
            tuple(sorted(EVIDENCE_FIELD_NAMES)),
        ).fetchall()
    finally:
        connection.close()

    history_by_ticket: dict[str, list[dict[str, object]]] = {}
    for row in history_rows:
        row_dict = dict(row)
        history_by_ticket.setdefault(_text(row_dict["defect_id"]), []).append(row_dict)
    return [
        build_ticket_record(dict(row), history_by_ticket.get(_text(row["defect_id"]), []))
        for row in rows
    ]


def _safe_excel_value(value: object) -> object:
    if not isinstance(value, str):
        return value
    return "'" + value if value[:1] in ("=", "+", "-", "@") else value


def _sheet_widths() -> tuple[int, ...]:
    return (
        34,
        76,
        14,
        64,
        23,
        20,
        28,
        28,
        28,
        22,
        22,
        58,
        18,
        30,
        18,
        32,
        24,
        18,
        18,
        30,
        30,
        22,
        16,
        16,
        30,
        24,
        26,
        16,
        50,
        50,
        50,
        70,
        42,
        80,
        60,
        80,
        58,
        20,
        72,
        30,
        66,
        72,
        66,
    )


def _write_ticket_sheet(
    workbook: Workbook,
    *,
    title: str,
    records: Sequence[Mapping[str, object]],
    llm_summaries: Mapping[str, Mapping[str, object]],
) -> None:
    sheet = workbook.create_sheet(title)
    sheet.append(REPORT_COLUMNS)
    for record in sorted(
        records,
        key=lambda item: (
            _text(item.get("target_recorded_at")),
            _text(item.get("ticket_id")),
        ),
        reverse=True,
    ):
        report_row = build_report_row(
            record,
            llm_summaries.get(_text(record.get("ticket_id"))),
        )
        sheet.append(
            [_safe_excel_value(report_row[column]) for column in REPORT_COLUMNS]
        )
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = "A1:{}{}".format(
        get_column_letter(len(REPORT_COLUMNS)),
        max(sheet.max_row, 1),
    )
    widths = _sheet_widths()
    for index in range(1, len(REPORT_COLUMNS) + 1):
        width = widths[index - 1] if index <= len(widths) else 24
        sheet.column_dimensions[get_column_letter(index)].width = width


def write_open_ticket_postponement_report(
    records: Sequence[Mapping[str, object]],
    *,
    output_path: str | Path,
    llm_summaries: Mapping[str, Mapping[str, object]] | None = None,
    source_snapshot: str = "",
) -> Path:
    summaries = llm_summaries or {}
    records_by_classification: dict[str, list[Mapping[str, object]]] = {}
    for record in records:
        classification = _text(record.get("classification"))
        records_by_classification.setdefault(classification, []).append(record)

    workbook = Workbook()
    definitions = workbook.active
    definitions.title = "00_Definitions"
    counts = Counter(_text(record.get("classification")) for record in records)
    definition_rows = (
        ("Source snapshot", source_snapshot or "Not recorded"),
        ("Open ticket definition", "Phase code is not 06 or 09."),
        ("Confirmed postponed", "Latest Target I-Step is later than the release parsed from reported software."),
        ("Requested target review", "Only Requested Target I-Step is later; it is not a confirmed Target I-Step."),
        ("Same target", "Source and Target I-Step resolve to the same YY-03, YY-07, or YY-11 release."),
        ("Earlier target", "Target I-Step resolves to an earlier release than the reported software and needs data checking."),
        ("Manual version comparison", "A concrete Target I-Step exists, but source or target software has no standard YY-03/07/11 release token. This is not a postponement conclusion."),
        ("Target software", "The full Target I-Step display value is the recorded target software; target week and commercial solution date are separate scheduling evidence."),
        ("LLM assessment", "Evidence-bounded candidate summary. It does not establish root cause, delivery completion, approval, customer impact, or safety impact."),
    )
    definitions.append(("Definition", "Value"))
    for row in definition_rows:
        definitions.append(row)
    definitions.append(("Confirmed postponed ticket count", counts["confirmed_postponed"]))
    definitions.append(("Requested target review ticket count", counts["requested_target_review"]))
    definitions.append(("Same target ticket count", counts["same_target_not_postponed"]))
    definitions.append(("Earlier target data-check count", counts["earlier_target_data_check"]))
    definitions.append(("Manual version comparison count", counts["needs_manual_version_comparison"]))
    for cell in definitions[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
    definitions.column_dimensions["A"].width = 38
    definitions.column_dimensions["B"].width = 120
    definitions.freeze_panes = "A2"
    for row in definitions.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    for sheet_title, classification in SHEET_CLASSIFICATIONS:
        _write_ticket_sheet(
            workbook,
            title=sheet_title,
            records=records_by_classification.get(classification, []),
            llm_summaries=summaries,
        )

    resolved_output_path = Path(output_path)
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(resolved_output_path)
    return resolved_output_path


def _record_flag(record: Mapping[str, object], name: str) -> bool:
    return bool(record.get(name))


def _is_target_data_check(record: Mapping[str, object]) -> bool:
    return _text(record.get("classification")) in {
        "same_target_not_postponed",
        "earlier_target_data_check",
        "needs_manual_version_comparison",
    }


def shift_confidence_category(record: Mapping[str, object]) -> tuple[str, str]:
    if _record_flag(record, "has_later_target_i_step") or _record_flag(
        record, "has_later_shift_to_set"
    ):
        evidence = []
        if _record_flag(record, "has_later_target_i_step"):
            evidence.append("Target I-Step")
        if _record_flag(record, "has_later_shift_to_set"):
            evidence.append("Shift-to-Set")
        return (
            "Confirmed PU Shift",
            "{} is later than the reported software PU.".format(" / ".join(evidence)),
        )
    if _text(record.get("phase")).startswith("07") and _record_flag(
        record, "has_concrete_phase07_fix_version"
    ):
        return (
            "Phase 07 Concrete Fix-Version Plan",
            "Phase 07 records a concrete Closed in version, SW-HRL, or App version.",
        )
    if _record_flag(record, "has_later_requested_target"):
        return (
            "Requested Target Review",
            "Requested Target I-Step is later, but no confirmed Target I-Step or Shift-to-Set is recorded.",
        )
    if _record_flag(record, "has_later_supplier_confirmed_i_step"):
        return (
            "Supplier-Confirmed Future Target",
            "Supplier-confirmed I-Step is later, without stronger confirmed shift evidence.",
        )
    if _record_flag(record, "has_later_predicted_target_i_step"):
        return (
            "Predicted Future Target",
            "Predicted Target I-Step is later, without stronger shift evidence.",
        )
    if _record_flag(record, "has_later_first_use_sop"):
        return (
            "First Use/SOP Future Plan",
            "First Use/SOP PU is later; this is future applicability evidence, not delivery proof.",
        )
    if _text(record.get("phase")).startswith("07") and _text(
        record.get("phase07_fix_versions")
    ):
        return (
            "Phase 07 Closure Evidence - Verify",
            "Phase 07 has a generic Closed in version label; verify the concrete software version.",
        )
    if _is_target_data_check(record):
        return (
            "Target Data Check",
            "Target evidence is equal, earlier, or not comparable to the reported software PU.",
        )
    return ("Not Classified", "No qualifying shift or future-plan evidence was retained.")


def _is_shift_plan_candidate(record: Mapping[str, object]) -> bool:
    return any(
        (
            _record_flag(record, "has_later_target_i_step"),
            _record_flag(record, "has_later_shift_to_set"),
            _record_flag(record, "has_later_first_use_sop"),
            _record_flag(record, "has_later_supplier_confirmed_i_step"),
            _record_flag(record, "has_later_predicted_target_i_step"),
            _record_flag(record, "has_later_requested_target"),
            _text(record.get("phase")).startswith("07")
            and bool(_text(record.get("phase07_fix_versions"))),
            _is_target_data_check(record),
        )
    )


def select_open_shift_ticket_records(
    records: Sequence[Mapping[str, object]],
) -> list[Mapping[str, object]]:
    return [record for record in records if _is_shift_plan_candidate(record)]


def write_open_shift_ticket_report(
    records: Sequence[Mapping[str, object]],
    *,
    output_path: str | Path,
    llm_summaries: Mapping[str, Mapping[str, object]] | None = None,
    source_snapshot: str = "",
) -> Path:
    candidates = select_open_shift_ticket_records(records)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Open_Shift_Tickets"
    summaries = llm_summaries or {}
    category_counts = Counter(
        shift_confidence_category(record)[0] for record in candidates
    )
    sheet.append(("Open Shift Ticket Analysis", ""))
    sheet.append(("Source snapshot", source_snapshot or "Not recorded"))
    sheet.append(("Scope", "Open tickets only: Phase code is not 06 or 09."))
    sheet.append(("Candidate tickets", len(candidates)))
    sheet.append(("Category rule", "Each ticket belongs to exactly one highest-confidence category."))
    sheet.append(())
    sheet.append(("Category", "Ticket Count"))
    for category in SHIFT_CONFIDENCE_CATEGORY_ORDER:
        sheet.append(
            (
                category,
                category_counts[category],
            )
        )
    sheet.append(())
    sheet.append(("Filtering", "Use the Confidence Category and Confidence Basis columns in the table below."))
    sheet.append(())
    sheet.append(REPORT_COLUMNS)
    header_row = sheet.max_row
    category_rank = {
        category: index for index, category in enumerate(SHIFT_CONFIDENCE_CATEGORY_ORDER)
    }
    for record in sorted(
        candidates,
        key=lambda item: (
            category_rank.get(shift_confidence_category(item)[0], len(category_rank)),
            _text(item.get("target_recorded_at")),
            _text(item.get("ticket_id")),
        ),
        reverse=False,
    ):
        report_row = build_report_row(
            record,
            summaries.get(_text(record.get("ticket_id"))),
        )
        sheet.append(
            [_safe_excel_value(report_row[column]) for column in REPORT_COLUMNS]
        )
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
    for cell in sheet[7]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="5B9BD5")
    for cell in sheet[header_row]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.row_dimensions[1].height = 24
    for row_number in range(2, header_row):
        sheet.row_dimensions[row_number].height = 18
        for cell in sheet[row_number]:
            cell.alignment = Alignment(wrap_text=False, vertical="center")
    for row_number in range(header_row + 1, sheet.max_row + 1):
        sheet.row_dimensions[row_number].height = 42
        for cell in sheet[row_number]:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.freeze_panes = None
    sheet.sheet_view.zoomScale = 85
    for row in sheet.iter_rows(min_row=header_row + 1):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.auto_filter.ref = "A{}:{}{}".format(
        header_row,
        get_column_letter(len(REPORT_COLUMNS)),
        sheet.max_row,
    )
    widths = _sheet_widths()
    for index in range(1, len(REPORT_COLUMNS) + 1):
        width = widths[index - 1] if index <= len(widths) else 24
        sheet.column_dimensions[get_column_letter(index)].width = width
    resolved_output_path = Path(output_path)
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(resolved_output_path)
    return resolved_output_path


def write_llm_input_jsonl(
    records: Sequence[Mapping[str, object]],
    output_path: str | Path,
) -> list[dict[str, object]]:
    input_records = [build_llm_input_record(record) for record in records]
    resolved_output_path = Path(output_path)
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_output_path.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            for record in input_records
        ),
        encoding="utf-8",
    )
    return input_records


def load_validated_llm_summaries(
    input_records: Sequence[Mapping[str, object]],
    summaries_path: str | Path | None,
) -> dict[str, dict[str, object]]:
    if summaries_path is None:
        return {}
    resolved_summaries_path = Path(summaries_path)
    if not resolved_summaries_path.exists():
        return {}
    expected_fingerprints = {
        _text(record.get("ticket_id")): _text(record.get("input_fingerprint"))
        for record in input_records
        if _text(record.get("ticket_id")) and _text(record.get("input_fingerprint"))
    }
    input_versions = {
        _text(record.get("ticket_id")): _text(record.get("input_version"))
        for record in input_records
        if _text(record.get("ticket_id"))
    }
    summaries: dict[str, dict[str, object]] = {}
    for line_number, line in enumerate(
        resolved_summaries_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            raw_summary = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(
                "Invalid LLM summary JSONL at {}:{}".format(
                    resolved_summaries_path,
                    line_number,
                )
            ) from error
        if not isinstance(raw_summary, Mapping):
            continue
        ticket_id = _text(raw_summary.get("ticket_id"))
        if (
            not ticket_id
            or _text(raw_summary.get("source")).casefold() != "llm"
            or _text(raw_summary.get("input_fingerprint"))
            != expected_fingerprints.get(ticket_id, "")
        ):
            continue
        required_fields = [
            "issue_summary",
            "postponement_assessment",
            "review_focus",
        ]
        if input_versions.get(ticket_id) == "open-shift-ticket-plan-llm-v3":
            required_fields.extend(
                (
                    "postponement_reason_analysis",
                    "resolution_timing_analysis",
                    "resolution_version_analysis",
                )
            )
        if not all(_text(raw_summary.get(field_name)) for field_name in required_fields):
            continue
        summaries[ticket_id] = dict(raw_summary)
    return summaries


def write_confirmed_postponed_csv(
    records: Sequence[Mapping[str, object]],
    *,
    output_path: str | Path,
    llm_summaries: Mapping[str, Mapping[str, object]] | None = None,
) -> Path:
    summaries = llm_summaries or {}
    resolved_output_path = Path(output_path)
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    confirmed_records = [
        record
        for record in records
        if _text(record.get("classification")) == "confirmed_postponed"
    ]
    with resolved_output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_COLUMNS)
        writer.writeheader()
        for record in sorted(
            confirmed_records,
            key=lambda item: (
                _text(item.get("target_recorded_at")),
                _text(item.get("ticket_id")),
            ),
            reverse=True,
        ):
            report_row = build_report_row(
                record,
                summaries.get(_text(record.get("ticket_id"))),
            )
            writer.writerow(
                {
                    column: _safe_excel_value(report_row[column])
                    for column in REPORT_COLUMNS
                }
            )
    return resolved_output_path