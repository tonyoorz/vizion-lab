from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Mapping, Sequence

from backend.analytics.ingest.source_store import normalize_defect_history_evidence
from backend.analytics.open_ticket_postponement import (
    EVIDENCE_FIELD_NAMES,
    bounded_text,
    build_ticket_record,
    clean_text,
)


CURRENT_STATE_INPUT_VERSION = "shift-current-evidence-v2"
STATUS_VALUES = {
    "historical_shift_status": {"documented", "not_supported", "uncertain"},
    "current_postponement_status": {"pending", "not_pending", "unclear"},
    "repair_status": {"no_fix_recorded", "fix_reported", "testing", "verification_pending", "verified", "recurrence_reported", "conflicting", "unknown"},
    "delivery_status": {"planned", "integrated", "delivered", "conflicting", "unknown"},
}
ANALYSIS_FIELDS = (
    "issue_summary", "postponement_reason_analysis", "resolution_timing_analysis",
    "resolution_version_analysis", "review_focus", "current_state_summary",
    "root_cause_analysis", "recovery_analysis",
)
COMMENT_RELEVANCE = re.compile(
    r"fix|solv|resolv|retest|verif|recur|tolerat|shift|retract|another ticket|wrong ticket|"
    r"liefer|behob|geloest|falsche|anderes ticket|zurueck|korrig|nicht korrekt|not correct",
    re.IGNORECASE,
)


def _text(value: object) -> str:
    return str(value or "").strip()


def build_current_evidence_input(
    ticket: Mapping[str, Any],
    history: Sequence[Mapping[str, Any]],
    *,
    source_fingerprint: str,
) -> dict[str, Any]:
    derived = build_ticket_record(ticket, history)
    ticket_id = _text(derived["ticket_id"])
    comments = json.loads(_text(ticket.get("comments")) or "[]")
    comments.sort(key=lambda item: (_text(item.get("creation_time")), _text(item.get("id"))))
    selected = set(range(max(0, len(comments) - 24), len(comments)))
    relevant = [index for index, comment in enumerate(comments) if COMMENT_RELEVANCE.search(clean_text(comment.get("text")))]
    selected.update(relevant[-16:])
    comment_limit = max(300, min(1200, 18000 // max(1, len(selected))))
    plan_fields = {
        key: derived.get(key, "")
        for key in (
            "phase", "software_version", "reported_release", "target_i_step", "target_release",
            "target_recorded_at", "shift_to_set_pu", "first_use_sop_pu", "closed_in_version",
            "sw_hrl_closed_version", "app_version", "target_week", "postponement_decision_date",
            "postponement_document", "requested_target_release", "supplier_confirmed_i_step",
            "predicted_target_i_step", "phase07_fix_version_quality",
        )
    }
    evidence = [
        {"id": "M", "date": _text(ticket.get("last_modified")), "source": "current_metadata", "text": json.dumps(plan_fields, ensure_ascii=False)},
        {"id": "D", "date": _text(ticket.get("last_modified")), "source": "description_snapshot", "text": bounded_text(ticket.get("description"), 16000)},
    ]
    for index in sorted(selected):
        comment = comments[index]
        evidence.append({
            "id": "C-" + (_text(comment.get("id")) or str(index)),
            "date": _text(comment.get("creation_time") or comment.get("last_modified")),
            "modified_at": _text(comment.get("last_modified")),
            "source": "comment",
            "text": bounded_text(comment.get("text"), comment_limit),
        })
    relevant_history = [
        event for event in history
        if _text(event.get("field_name")) in EVIDENCE_FIELD_NAMES | {"phase", "software_version", "description"}
    ]
    relevant_history.sort(key=lambda event: (_text(event.get("event_timestamp")), int(event.get("entry_index") or 0), int(event.get("change_index") or 0)))
    for index, event in enumerate(relevant_history[-20:]):
        evidence.append({
            "id": "H-" + str(index), "date": _text(event.get("event_timestamp")),
            "source": "history", "field": _text(event.get("field_name")),
            "text": bounded_text("{} -> {}".format(event.get("old_value_text") or event.get("old_value") or "(blank)", event.get("new_value_text") or event.get("new_value") or "(blank)"), 700),
        })
    payload = {
        "input_version": CURRENT_STATE_INPUT_VERSION,
        "ticket_id": ticket_id,
        "source_fingerprint": source_fingerprint,
        "ticket": {
            "title": derived["name"], "phase": derived["phase"],
            "source_refreshed_at": _text(ticket.get("evidence_refreshed_at")),
            "comment_count": len(comments), "included_comment_count": len(selected),
            "history_row_count": len(history), "included_history_count": min(20, len(relevant_history)),
            "evidence_boundary": "Bounded dated evidence; omitted or truncated evidence is not proof of absence.",
            "evidence": evidence,
        },
    }
    if ticket.get("source_mode") == "database_snapshot":
        payload["ticket"].pop("source_refreshed_at", None)
        payload["ticket"]["source_mode"] = "database_snapshot"
        payload["ticket"]["evidence_boundary"] = "Database snapshot evidence; original fetch timestamps are audited separately, not model facts. Online freshness and fetch completeness are not verified by this analysis."
    return {**payload, "input_fingerprint": sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()}


def load_current_evidence_inputs(
    database_path: Path,
    ticket_ids: Sequence[str],
    unavailable_ids: set[str],
    *,
    maximum_age_hours: int = 24,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    if not database_path.exists():
        raise ValueError("Current source database does not exist")
    scope = set(ticket_ids)
    if not unavailable_ids <= scope:
        raise ValueError("Unavailable IDs fall outside approved scope")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=maximum_age_hours)
    connection = sqlite3.connect(f"file:{database_path.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    inputs: list[dict[str, Any]] = []
    records: dict[str, dict[str, Any]] = {}
    snapshot_path = ""
    snapshot: dict[str, Any] = {}
    refreshed_at: list[str] = []
    comment_total = 0
    history_total = 0
    try:
        connection.execute("BEGIN")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"octane_defects", "octane_defect_comment_refresh_state", "octane_defect_history_events", "octane_ticket_evidence_refresh_state"}
        if not required <= tables:
            raise ValueError("Missing current-source fetch provenance; refresh the exact scope first")
        for ticket_id in sorted(scope - unavailable_ids):
            row = connection.execute(
                "SELECT d.*, e.refreshed_at AS evidence_refreshed_at, e.defect_last_modified AS evidence_last_modified, "
                "e.comments_count, e.history_count, e.evidence_fingerprint, e.snapshot_file, s.comments_json AS cache_comments "
                "FROM octane_defects d LEFT JOIN octane_ticket_evidence_refresh_state e ON e.defect_id=d.defect_id "
                "LEFT JOIN octane_defect_comment_refresh_state s ON s.defect_id=d.defect_id WHERE d.defect_id=?", (ticket_id,),
            ).fetchone()
            if row is None or not row["evidence_refreshed_at"]:
                raise ValueError(f"Missing current-source provenance for {ticket_id}")
            ticket = dict(row)
            timestamp = datetime.fromisoformat(_text(ticket["evidence_refreshed_at"]).replace("Z", "+00:00"))
            if timestamp.tzinfo is None or timestamp < cutoff:
                raise ValueError(f"Stale current-source evidence for {ticket_id}")
            if _text(ticket["last_modified"]) != _text(ticket["evidence_last_modified"]):
                raise ValueError(f"Metadata changed after evidence refresh for {ticket_id}")
            raw = json.loads(ticket["raw_json"])
            comments = json.loads(ticket["comments"] or "[]")
            if comments != json.loads(ticket["cache_comments"] or "null") or raw.get("comments") != comments or len(comments) != ticket["comments_count"]:
                raise ValueError(f"Canonical comment consistency failed for {ticket_id}")
            if ticket["snapshot_file"] != snapshot_path:
                snapshot_path = ticket["snapshot_file"]
                snapshot = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
            fingerprint = sha256(json.dumps({"defect": raw, "history": snapshot["histories"][ticket_id]}, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
            if fingerprint != ticket["evidence_fingerprint"]:
                raise ValueError(f"Raw evidence fingerprint mismatch for {ticket_id}")
            history = [dict(item) for item in connection.execute("SELECT * FROM octane_defect_history_events WHERE defect_id=?", (ticket_id,))]
            if len(history) != ticket["history_count"]:
                raise ValueError(f"Incomplete local history for {ticket_id}")
            expected_history = normalize_defect_history_evidence(snapshot["histories"][ticket_id])
            expected_history.sort(key=lambda event: (event["entry_index"], event["change_index"]))
            ordered_history = sorted(history, key=lambda event: (event["entry_index"], event["change_index"]))
            if len(expected_history) != len(ordered_history) or any(
                any(actual.get(field) != value for field, value in expected.items())
                for actual, expected in zip(ordered_history, expected_history)
            ):
                raise ValueError(f"Local history content differs from fetched evidence for {ticket_id}")
            derived = build_ticket_record(ticket, history)
            records[ticket_id] = {**ticket, **derived}
            inputs.append(build_current_evidence_input(ticket, history, source_fingerprint=fingerprint))
            refreshed_at.append(ticket["evidence_refreshed_at"])
            comment_total += len(comments)
            history_total += len(history)
    finally:
        connection.close()
    audit = {
        "requested_ticket_count": len(scope), "current_verified_ticket_count": len(inputs),
        "unavailable_ticket_ids": sorted(unavailable_ids), "comment_count": comment_total,
        "history_row_count": history_total, "earliest_evidence_refresh": min(refreshed_at, default=""),
        "latest_evidence_refresh": max(refreshed_at, default=""), "maximum_age_hours": maximum_age_hours,
        "canonical_source_verified": True,
    }
    return inputs, records, audit


def apply_current_evidence(
    baseline_records: Sequence[Mapping[str, Any]],
    summaries: Mapping[str, Mapping[str, Any]],
    inputs: Sequence[Mapping[str, Any]],
    current_records: Mapping[str, Mapping[str, Any]],
    unavailable_ids: set[str],
) -> list[dict[str, Any]]:
    inputs_by_id = {item["ticket_id"]: item for item in inputs}
    result = []
    for baseline in baseline_records:
        record = dict(baseline)
        ticket_id = _text(record["ticket_id"])
        if ticket_id in unavailable_ids:
            record.update({
                "historical_future_resolution_plan": _text(record.get("future_resolution_plan")),
                "historical_postponement_assessment": _text(record.get("postponement_assessment")),
                "source_evidence_status": "unavailable_preserved", "ticket_lifecycle_status": "Historical / online unavailable",
                "historical_shift_status": "uncertain", "current_postponement_status": "unclear",
                "repair_status": "unknown", "delivery_status": "unknown", "status_evidence": {},
                "current_state_summary": "Online metadata was not returned; historical local evidence retained, not reassessed as current.",
                "future_resolution_plan": "Historical-only evidence: online unavailable; no current plan reassessed.",
                "postponement_assessment": "Historical-only evidence: online unavailable; no current postponement reassessed.",
                "current_evidence_references": [], "evidence_fingerprint": "", "evidence_refreshed_at": "",
                "llm_input_fingerprint": "", "llm_input_version": "", "assessment_warnings": [],
                "future_resolution_status": "Historical evidence only - online unavailable", "llm_source": "not_reassessed", "llm_model": "",
                "confidence_category": "Historical source unavailable - not reassessed",
                "resolution_verification_status": "source_unavailable", "resolution_verification_summary": "Current state not reassessed: source unavailable.",
            })
            for field in ("postponement_reason_analysis", "resolution_timing_analysis", "resolution_version_analysis", "rca_root_cause", "rca_recovery"):
                record[field] = "Historical source only; no current assessment."
            result.append(record)
            continue
        summary = summaries.get(ticket_id, {})
        input_record = inputs_by_id.get(ticket_id, {})
        if not input_record or summary.get("input_fingerprint") != input_record.get("input_fingerprint"):
            raise ValueError(f"Current summary fingerprint mismatch for {ticket_id}")
        if summary.get("input_version") != CURRENT_STATE_INPUT_VERSION or summary.get("source") != "llm":
            raise ValueError(f"Invalid current summary version/source for {ticket_id}")
        evidence = {item["id"]: item for item in input_record["ticket"]["evidence"]}
        for field, values in STATUS_VALUES.items():
            references = summary.get("status_evidence", {}).get(field)
            if summary.get(field) not in values or not isinstance(references, list) or not set(references) <= set(evidence):
                raise ValueError(f"Invalid status/evidence for {ticket_id}: {field}")
            if summary[field] not in {"not_supported", "uncertain", "unclear", "unknown"} and not references:
                raise ValueError(f"Missing status evidence for {ticket_id}: {field}")
            if field in {"repair_status", "delivery_status"} and summary[field] not in {"unknown", "no_fix_recorded"} and set(references) <= {"M"}:
                raise ValueError(f"Metadata alone cannot prove repair/delivery for {ticket_id}")
        for field in ANALYSIS_FIELDS:
            if not _text(summary.get(field)):
                raise ValueError(f"Missing current analysis for {ticket_id}: {field}")
        current = current_records[ticket_id]
        phase = _text(current["phase"])
        record.update({key: current.get(key, "") for key in (
            "creation_time", "phase", "target_i_step", "target_release", "shift_to_set_pu", "first_use_sop_pu",
            "closed_in_version", "sw_hrl_closed_version", "app_version", "function_responsible", "solution_cluster",
            "product_areas", "defect_category", "assigned_ecu", "description", "comments", "plan_evidence_signals",
            "evidence_refreshed_at", "evidence_fingerprint",
        )})
        record.update({field: summary[field] for field in (*ANALYSIS_FIELDS, *STATUS_VALUES, "status_evidence")})
        cited = sorted({reference for values in summary["status_evidence"].values() for reference in values})
        record.update({
            "title": _text(current.get("name")), "source_software_version": _text(current.get("software_version")),
            "source_release": _text(current.get("reported_release")),
            "ticket_lifecycle_status": "Closed / Phase " + phase[:2] if phase[:2] in {"06", "09"} else "Open / active",
            "source_evidence_status": "database_snapshot" if current.get("source_mode") == "database_snapshot" else "current_verified", "llm_source": "llm", "llm_model": _text(summary.get("model")),
            "llm_input_fingerprint": input_record["input_fingerprint"], "llm_input_version": CURRENT_STATE_INPUT_VERSION,
            "assessment_warnings": list(summary.get("assessment_warnings") or []),
            "postponement_assessment": summary["current_state_summary"],
            "future_resolution_status": "Current evidence LLM assessment - not approved repair",
            "future_resolution_plan": summary["current_state_summary"] + "\n" + summary["resolution_version_analysis"],
            "rca_root_cause": summary["root_cause_analysis"], "rca_recovery": summary["recovery_analysis"],
            "rca_evidence_source": "Dated local evidence; LLM assessment, not verified diagnosis",
            "resolution_verification_status": "llm_evidence_" + summary["repair_status"],
            "resolution_verification_summary": summary["current_state_summary"],
            "current_evidence_references": [{"id": reference, **evidence[reference]} for reference in cited],
            "post_target_recurrence_candidates": [],
        })
        if current.get("source_mode") == "database_snapshot":
            record["future_resolution_status"] = "Database snapshot LLM assessment - not verified current Octane coverage"
            record["assessment_warnings"].append("Offline database snapshot: online freshness and fetch completeness are not verified; absence of evidence is not evidence of absence.")
        record["confidence_category"] = {
            "documented": "Historical Shift/Tolerate evidence recorded",
            "not_supported": "Historical Shift/Tolerate not supported by bounded evidence",
            "uncertain": "Historical Shift/Tolerate requires review",
        }[summary["historical_shift_status"]]
        record["confidence_basis"] = "Original cohort retained for traceability; current classification uses bounded dated evidence, not a keyword match or verified process approval."
        record["llm_current_postponement_status"] = summary["current_postponement_status"]
        conflict = (
            phase[:2] in {"06", "09"} and summary["current_postponement_status"] == "pending"
        ) or (
            phase[:2] not in {"06", "09"} and summary["current_postponement_status"] == "not_pending"
            and summary["delivery_status"] in {"planned", "conflicting"}
        )
        if conflict:
            record["current_postponement_status"] = "unclear"
            record["assessment_warnings"].append("Current postponement claim conflicts with lifecycle or outstanding delivery plan; downgraded for review")
            record["current_state_summary"] += "\n证据门禁：延期状态与当前阶段或未完成交付计划存在冲突，当前延期判断为待核实。"
            record["postponement_assessment"] = record["current_state_summary"]
            record["future_resolution_plan"] = record["current_state_summary"] + "\n" + summary["resolution_version_analysis"]
            record["resolution_verification_summary"] = record["current_state_summary"]
        result.append(record)
    return result