from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
from html import unescape as html_unescape
import json
from math import isfinite
from pathlib import Path
import re
import unicodedata
from typing import Any, Mapping, Sequence


NORMALIZATION_POLICY_VERSION = "phase09-issue-normalization-v1"
TARGET_CLOSURE_DISPOSITIONS = frozenset(
    {
        "Follow-up / remediation recorded",
        "Direct closure without auditable rationale or follow-up",
    }
)

HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
WHITESPACE_PATTERN = re.compile(r"\s+")
TICKET_REFERENCE_PATTERN = re.compile(
    r"(?<!\d)(?:defect\s*)?(?:d\s*-?\s*)?(\d{6,8})(?!\d)",
    re.IGNORECASE,
)
ERROR_DESCRIPTION_END_PATTERN = re.compile(
    r"\bend\s+of\s+error\s+description\b", re.IGNORECASE
)
PAIR_INPUT_POLICY_VERSION = "phase09-duplicate-pair-v1"
PAIR_ISSUE_EVIDENCE_MAX_CHARS = 1_800
PAIR_PROCESS_EVIDENCE_MAX_CHARS = 2_000
PAIR_REVIEW_MODELS = ("deepseek-v4-pro", "qwen3.7-max")
PAIR_REVIEW_AUTO_CONFIRM_CONFIDENCE = 0.95
PAIR_REVIEW_MAX_RATIONALE_CHARS = 600
PAIR_REVIEWER_POLICY_VERSION = "phase09-duplicate-pair-reviewer-v4"


def _text(value: object) -> str:
    return str(value or "").strip()


def _clean_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", _text(value))
    text = html_unescape(HTML_TAG_PATTERN.sub(" ", text))
    return WHITESPACE_PATTERN.sub(" ", text).strip()


def _normalized(value: object) -> str:
    return _clean_text(value).casefold()


def _known(value: object) -> bool:
    return _normalized(value) not in {"", "(blank)", "not recorded", "unknown"}


def _ticket_id(value: object) -> str:
    return _text(value)


def extract_ticket_reference_ids(value: object) -> tuple[str, ...]:
    """Extract only numeric defect IDs from unstructured relationship text."""
    seen: set[str] = set()
    references: list[str] = []
    for match in TICKET_REFERENCE_PATTERN.finditer(_text(value)):
        ticket_id = match.group(1)
        if ticket_id not in seen:
            seen.add(ticket_id)
            references.append(ticket_id)
    return tuple(references)


def _function_context(ticket: Mapping[str, object]) -> str:
    for field_name in (
        "solution_cluster",
        "defect_category",
        "product_areas",
        "function2modul",
    ):
        value = _normalized(ticket.get(field_name))
        if value and value not in {"(blank)", "not recorded", "unknown"}:
            return value
    return ""


def _is_formal_duplicate_link(ticket: Mapping[str, object]) -> bool:
    return (
        _normalized(ticket.get("effective_reason")) == "child (duplicate)"
        and "child" in _normalized(ticket.get("parent_child_type"))
        and bool(extract_ticket_reference_ids(ticket.get("relation_to")))
    )


def _pair_key(left_ticket_id: str, right_ticket_id: str) -> tuple[str, str]:
    return tuple(sorted((left_ticket_id, right_ticket_id)))


def _known_conflict(left: object, right: object) -> bool:
    return _known(left) and _known(right) and _normalized(left) != _normalized(right)


def _hard_contradictions(
    left: Mapping[str, object],
    right: Mapping[str, object],
) -> list[str]:
    contradictions: list[str] = []
    if _known_conflict(left.get("final_project"), right.get("final_project")):
        contradictions.append("final_project_conflict")
    if _known_conflict(left.get("assigned_ecu"), right.get("assigned_ecu")):
        contradictions.append("assigned_ecu_conflict")
    if _known_conflict(_function_context(left), _function_context(right)):
        contradictions.append("function_context_conflict")
    return contradictions


def _context_compatible(left: Mapping[str, object], right: Mapping[str, object]) -> bool:
    if _hard_contradictions(left, right):
        return False
    return _known(left.get("assigned_ecu")) and _known(_function_context(left))


def _append_pair_channel(
    channels_by_pair: dict[tuple[str, str], set[str]],
    left_ticket_id: str,
    right_ticket_id: str,
    channel: str,
) -> None:
    if left_ticket_id == right_ticket_id:
        return
    channels_by_pair.setdefault(_pair_key(left_ticket_id, right_ticket_id), set()).add(channel)


def _bounded_text(value: object, maximum: int) -> str:
    text = _clean_text(value)
    if len(text) <= maximum:
        return text
    return text[: maximum - 3].rstrip() + "..."


def _core_issue_evidence(ticket: Mapping[str, object]) -> str:
    source_text = ticket.get("description_full") or ticket.get("description")
    text = _clean_text(source_text)
    if (match := ERROR_DESCRIPTION_END_PATTERN.search(text)) is not None:
        text = text[: match.start()].strip()
    return _bounded_text(text, PAIR_ISSUE_EVIDENCE_MAX_CHARS)


def _append_limited(parts: list[str], value: str, maximum: int) -> None:
    if not value:
        return
    joined_length = len("\n".join(parts))
    if joined_length >= maximum:
        return
    available = maximum - joined_length - (1 if parts else 0)
    parts.append(value[:available].rstrip() + ("..." if len(value) > available else ""))


def _process_evidence(ticket: Mapping[str, object]) -> str:
    parts: list[str] = []
    raw_comments = ticket.get("comment_records")
    if isinstance(raw_comments, list):
        for comment in raw_comments[-5:]:
            if not isinstance(comment, Mapping):
                continue
            comment_text = _bounded_text(comment.get("raw_text"), 700)
            if comment_text:
                _append_limited(
                    parts,
                    "[Comment | {} | {}] {}".format(
                        _text(comment.get("timestamp")) or "Not recorded",
                        _text(comment.get("author")) or "Not recorded",
                        comment_text,
                    ),
                    PAIR_PROCESS_EVIDENCE_MAX_CHARS,
                )
    raw_history = ticket.get("audit_history_events")
    if isinstance(raw_history, list):
        for event in raw_history[-12:]:
            if not isinstance(event, Mapping):
                continue
            _append_limited(
                parts,
                "[History | {} | {} | {}] {} -> {}".format(
                    _text(event.get("timestamp")) or "Not recorded",
                    _text(event.get("actor")) or "Not recorded",
                    _text(event.get("field_name")) or "Not recorded",
                    _bounded_text(event.get("old_value"), 300) or "(blank)",
                    _bounded_text(event.get("new_value"), 300) or "(blank)",
                ),
                PAIR_PROCESS_EVIDENCE_MAX_CHARS,
            )
    if not parts:
        return "No retained comments or closure-history events."
    return "\n".join(parts)


def _pair_ticket_evidence(ticket: Mapping[str, object]) -> dict[str, str]:
    return {
        "title": _bounded_text(ticket.get("name"), 320) or "Not recorded",
        "function_context": _function_context(ticket) or "Not recorded",
        "assigned_ecu": _clean_text(ticket.get("assigned_ecu")) or "Not recorded",
        "software_version": _bounded_text(ticket.get("software_version"), 220)
        or "Not recorded",
        "closure_disposition": _clean_text(ticket.get("closure_disposition"))
        or "Not recorded",
        "issue_evidence": _core_issue_evidence(ticket) or "No retained issue text.",
        "process_evidence": _process_evidence(ticket),
    }


def _ticket_evidence_records(
    side: str,
    ticket_id: str,
    ticket_evidence: Mapping[str, str],
) -> list[dict[str, str]]:
    evidence_types = {
        "title": "ticket_title",
        "function_context": "ticket_function_context",
        "assigned_ecu": "ticket_assigned_ecu",
        "software_version": "ticket_software_version",
        "closure_disposition": "ticket_closure_disposition",
        "issue_evidence": "bounded_issue_evidence",
        "process_evidence": "bounded_process_evidence",
    }
    return [
        {
            "evidence_id": f"{side}_{field_name}",
            "type": evidence_type,
            "ticket_side": side,
            "ticket_id": ticket_id,
            "value": ticket_evidence[field_name],
        }
        for field_name, evidence_type in evidence_types.items()
    ]


def _pair_input_fingerprint(record: Mapping[str, object]) -> str:
    fingerprint_payload = {
        key: value
        for key, value in record.items()
        if key not in {"input_fingerprint", "pair_id"}
    }
    return sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _integer_or_high(value: object) -> int:
    text = _text(value)
    return int(text) if text.isdigit() else 10**18


def _evidence_richness(ticket: Mapping[str, object]) -> tuple[int, int, int]:
    description = _normalized(ticket.get("description") or ticket.get("description_full"))
    comments = _normalized(ticket.get("comments"))
    comment_count = ticket.get("comment_count")
    history_count = ticket.get("audit_history_event_count")
    try:
        recorded_comment_count = int(comment_count or 0)
    except (TypeError, ValueError):
        recorded_comment_count = 0
    try:
        recorded_history_count = int(history_count or 0)
    except (TypeError, ValueError):
        recorded_history_count = 0
    return (
        len(description) + len(comments),
        recorded_comment_count,
        recorded_history_count,
    )


def _representative_sort_key(ticket: Mapping[str, object]) -> tuple[int, int, int, str, int]:
    description_length, comment_count, history_count = _evidence_richness(ticket)
    return (
        -description_length,
        -comment_count,
        -history_count,
        _text(ticket.get("creation_time")) or "9999-99-99T99:99:99Z",
        _integer_or_high(ticket.get("ticket_id")),
    )


def _formal_parent_context_key(ticket: Mapping[str, object]) -> tuple[str, str, str, str] | None:
    if not _is_formal_duplicate_link(ticket):
        return None
    parent_ids = extract_ticket_reference_ids(ticket.get("relation_to"))
    assigned_ecu = _normalized(ticket.get("assigned_ecu"))
    function_context = _function_context(ticket)
    final_project = _normalized(ticket.get("final_project"))
    if len(parent_ids) != 1 or not assigned_ecu or not function_context or not final_project:
        return None
    return (parent_ids[0], final_project, assigned_ecu, function_context)


def build_strict_duplicate_candidates(
    tickets: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Build review candidates without merging tickets.

    Candidate channels are intentionally broad enough for later evidence review,
    while ``eligible_for_llm_review`` is limited to pairs with a verified formal
    duplicate relationship and compatible technical context. A title match alone
    is deliberately quarantined rather than passed to merge review.
    """
    tickets_by_id: dict[str, Mapping[str, object]] = {}
    formal_parent_groups: dict[str, list[str]] = defaultdict(list)
    title_context_groups: dict[tuple[str, str, str], list[str]] = defaultdict(list)

    for ticket in tickets:
        ticket_id = _ticket_id(ticket.get("ticket_id"))
        if not ticket_id:
            raise ValueError("Phase 09 normalization ticket is missing ticket_id")
        if ticket_id in tickets_by_id:
            raise ValueError(f"Duplicate Phase 09 normalization ticket id: {ticket_id}")
        tickets_by_id[ticket_id] = ticket

        if _is_formal_duplicate_link(ticket):
            for parent_id in extract_ticket_reference_ids(ticket.get("relation_to")):
                formal_parent_groups[parent_id].append(ticket_id)

        title = _normalized(ticket.get("name"))
        assigned_ecu = _normalized(ticket.get("assigned_ecu"))
        function_context = _function_context(ticket)
        if title and assigned_ecu and function_context:
            title_context_groups[(title, assigned_ecu, function_context)].append(ticket_id)

    channels_by_pair: dict[tuple[str, str], set[str]] = {}
    parent_ids_by_pair: dict[tuple[str, str], set[str]] = defaultdict(set)
    for parent_id, member_ids in formal_parent_groups.items():
        for left_index, left_ticket_id in enumerate(sorted(member_ids)):
            for right_ticket_id in sorted(member_ids)[left_index + 1 :]:
                pair_key = _pair_key(left_ticket_id, right_ticket_id)
                _append_pair_channel(
                    channels_by_pair,
                    left_ticket_id,
                    right_ticket_id,
                    "shared_verified_duplicate_parent",
                )
                parent_ids_by_pair[pair_key].add(parent_id)

    for member_ids in title_context_groups.values():
        for left_index, left_ticket_id in enumerate(sorted(member_ids)):
            for right_ticket_id in sorted(member_ids)[left_index + 1 :]:
                _append_pair_channel(
                    channels_by_pair,
                    left_ticket_id,
                    right_ticket_id,
                    "exact_normalized_title_and_context",
                )

    candidates: list[dict[str, object]] = []
    for left_ticket_id, right_ticket_id in sorted(channels_by_pair):
        left = tickets_by_id[left_ticket_id]
        right = tickets_by_id[right_ticket_id]
        channels = sorted(channels_by_pair[(left_ticket_id, right_ticket_id)])
        contradictions = _hard_contradictions(left, right)
        shared_parent_ids = sorted(parent_ids_by_pair[(left_ticket_id, right_ticket_id)])
        eligible_for_llm_review = (
            not contradictions
            and "shared_verified_duplicate_parent" in channels
            and bool(shared_parent_ids)
            and _context_compatible(left, right)
        )
        if contradictions:
            rejection_reason = "hard_context_contradiction"
        elif eligible_for_llm_review:
            rejection_reason = ""
        elif channels == ["exact_normalized_title_and_context"]:
            rejection_reason = "title_context_match_without_second_identity_anchor"
        else:
            rejection_reason = "insufficient_verified_duplicate_evidence"
        candidates.append(
            {
                "left_ticket_id": left_ticket_id,
                "right_ticket_id": right_ticket_id,
                "candidate_channels": channels,
                "shared_duplicate_parent_ids": shared_parent_ids,
                "hard_contradictions": contradictions,
                "eligible_for_llm_review": eligible_for_llm_review,
                "rejection_reason": rejection_reason,
            }
        )
    return candidates


def build_representative_anchored_review_pairs(
    tickets: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Return only representative-to-member pairs for dual LLM duplicate review.

    A common duplicate parent is not itself enough to merge tickets. This method
    limits review work to one evidence-rich in-scope representative per formally
    linked, technically compatible bucket. Every member must later independently
    pass dual LLM review against that representative; no transitive merge edge is
    created here.
    """
    tickets_by_id: dict[str, Mapping[str, object]] = {}
    parent_context_groups: dict[tuple[str, str, str, str], list[str]] = defaultdict(list)
    for ticket in tickets:
        ticket_id = _ticket_id(ticket.get("ticket_id"))
        if not ticket_id:
            raise ValueError("Phase 09 normalization ticket is missing ticket_id")
        if ticket_id in tickets_by_id:
            raise ValueError(f"Duplicate Phase 09 normalization ticket id: {ticket_id}")
        tickets_by_id[ticket_id] = ticket
        if (context_key := _formal_parent_context_key(ticket)) is not None:
            parent_context_groups[context_key].append(ticket_id)

    candidates_by_pair = {
        (candidate["left_ticket_id"], candidate["right_ticket_id"]): candidate
        for candidate in build_strict_duplicate_candidates(tickets)
    }
    review_pairs: list[dict[str, object]] = []
    for parent_context_key, member_ids in sorted(parent_context_groups.items()):
        if len(member_ids) < 2:
            continue
        representative_ticket_id = min(
            member_ids,
            key=lambda ticket_id: _representative_sort_key(tickets_by_id[ticket_id]),
        )
        for member_ticket_id in sorted(member_ids):
            if member_ticket_id == representative_ticket_id:
                continue
            left_ticket_id, right_ticket_id = _pair_key(
                representative_ticket_id, member_ticket_id
            )
            candidate = candidates_by_pair.get((left_ticket_id, right_ticket_id))
            if candidate is None or not candidate["eligible_for_llm_review"]:
                continue
            review_pairs.append(
                {
                    **candidate,
                    "representative_ticket_id": representative_ticket_id,
                    "formal_parent_id": parent_context_key[0],
                }
            )

    return sorted(
        review_pairs,
        key=lambda pair: (
            str(pair["representative_ticket_id"]),
            str(pair["left_ticket_id"]),
            str(pair["right_ticket_id"]),
        ),
    )


def build_pair_review_input_records(
    tickets: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Create bounded, evidence-cited inputs for independent pair reviewers."""
    tickets_by_id = {
        _ticket_id(ticket.get("ticket_id")): ticket
        for ticket in tickets
        if _ticket_id(ticket.get("ticket_id"))
    }
    if len(tickets_by_id) != len(tickets):
        raise ValueError("Phase 09 pair input tickets must have unique non-empty IDs")

    records: list[dict[str, object]] = []
    for pair in build_representative_anchored_review_pairs(tickets):
        left_ticket_id = str(pair["left_ticket_id"])
        right_ticket_id = str(pair["right_ticket_id"])
        representative_ticket_id = str(pair["representative_ticket_id"])
        left = tickets_by_id[left_ticket_id]
        right = tickets_by_id[right_ticket_id]
        formal_parent_id = str(pair["formal_parent_id"])
        assigned_ecu = _normalized(left.get("assigned_ecu"))
        function_context = _function_context(left)
        required_evidence = [
            {
                "evidence_id": f"formal_parent:{formal_parent_id}",
                "type": "formal_duplicate_parent",
                "value": formal_parent_id,
            },
            {
                "evidence_id": f"assigned_ecu:{assigned_ecu}",
                "type": "exact_assigned_ecu",
                "value": _clean_text(left.get("assigned_ecu")),
            },
            {
                "evidence_id": f"function:{function_context}",
                "type": "exact_function_context",
                "value": function_context,
            },
        ]
        left_evidence = _pair_ticket_evidence(left)
        right_evidence = _pair_ticket_evidence(right)
        evidence = [
            *required_evidence,
            *_ticket_evidence_records("ticket_a", left_ticket_id, left_evidence),
            *_ticket_evidence_records("ticket_b", right_ticket_id, right_evidence),
        ]
        record: dict[str, object] = {
            "policy_version": PAIR_INPUT_POLICY_VERSION,
            "representative_ticket_id": representative_ticket_id,
            "left_ticket_id": left_ticket_id,
            "right_ticket_id": right_ticket_id,
            "required_evidence_ids": [
                item["evidence_id"] for item in required_evidence
            ],
            "evidence": evidence,
            "left": left_evidence,
            "right": right_evidence,
        }
        pair_identity = "|".join(
            (
                PAIR_INPUT_POLICY_VERSION,
                representative_ticket_id,
                left_ticket_id,
                right_ticket_id,
                formal_parent_id,
            )
        )
        record["pair_id"] = "pair-" + sha256(pair_identity.encode("utf-8")).hexdigest()[:16]
        record["input_fingerprint"] = _pair_input_fingerprint(record)
        records.append(record)

    return records


def _ordered_ticket_ids(ticket_ids: Sequence[str]) -> list[str]:
    return sorted(ticket_ids, key=lambda ticket_id: (_integer_or_high(ticket_id), ticket_id))


def _review_string_list(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    values = [_text(item) for item in value]
    if any(not item for item in values) or len(values) != len(set(values)):
        return None
    return values


def _validated_completed_review(
    record: Mapping[str, object],
    pair_input: Mapping[str, object],
) -> dict[str, object] | None:
    if (
        _text(record.get("status")) != "completed"
        or _text(record.get("pair_id")) != _text(pair_input.get("pair_id"))
        or _text(record.get("input_fingerprint"))
        != _text(pair_input.get("input_fingerprint"))
        or _text(record.get("policy_version")) != _text(pair_input.get("policy_version"))
        or _text(record.get("review_policy_version"))
        != PAIR_REVIEWER_POLICY_VERSION
    ):
        return None
    model = _normalized(record.get("model"))
    if model not in PAIR_REVIEW_MODELS:
        return None
    outcome = record.get("outcome")
    if not isinstance(outcome, Mapping):
        return None
    verdict = _text(outcome.get("verdict"))
    if verdict not in {"SAME_UNDERLYING_DEFECT", "DISTINCT", "INSUFFICIENT_EVIDENCE"}:
        return None
    try:
        confidence = float(outcome.get("confidence"))
    except (TypeError, ValueError):
        return None
    if not isfinite(confidence) or not 0 <= confidence <= 1:
        return None
    positive_evidence_ids = _review_string_list(outcome.get("positive_evidence_ids"))
    contradiction_evidence_ids = _review_string_list(
        outcome.get("contradiction_evidence_ids")
    )
    verified_required_evidence_ids = _review_string_list(
        outcome.get("verified_required_evidence_ids")
    )
    rationale = _text(outcome.get("rationale"))
    if (
        positive_evidence_ids is None
        or contradiction_evidence_ids is None
        or not rationale
        or len(rationale) > PAIR_REVIEW_MAX_RATIONALE_CHARS
    ):
        return None
    evidence_ids = {
        _text(item.get("evidence_id"))
        for item in pair_input.get("evidence", [])
        if isinstance(item, Mapping) and _text(item.get("evidence_id"))
    }
    if (
        not set(positive_evidence_ids).issubset(evidence_ids)
        or not set(contradiction_evidence_ids).issubset(evidence_ids)
    ):
        return None
    if verdict == "SAME_UNDERLYING_DEFECT":
        required_evidence_values = _review_string_list(
            pair_input.get("required_evidence_ids")
        )
        required_evidence_ids = set(required_evidence_values or [])
        if (
            confidence < PAIR_REVIEW_AUTO_CONFIRM_CONFIDENCE
            or contradiction_evidence_ids
            or not required_evidence_ids
            or verified_required_evidence_ids is None
            or set(verified_required_evidence_ids) != required_evidence_ids
            or not any(
                evidence_id not in required_evidence_ids
                for evidence_id in positive_evidence_ids
            )
        ):
            return None
    return {
        "model": model,
        "verdict": verdict,
        "confidence": confidence,
        "positive_evidence_ids": positive_evidence_ids,
        "contradiction_evidence_ids": contradiction_evidence_ids,
        "verified_required_evidence_ids": verified_required_evidence_ids or [],
        "rationale": rationale,
    }


def _latest_reviews_by_pair(
    pair_inputs_by_id: Mapping[str, Mapping[str, object]],
    review_records: Sequence[Mapping[str, object]],
) -> dict[str, dict[str, dict[str, object]]]:
    reviews_by_pair: dict[str, dict[str, dict[str, object]]] = defaultdict(dict)
    for record in review_records:
        pair_id = _text(record.get("pair_id"))
        pair_input = pair_inputs_by_id.get(pair_id)
        if pair_input is None:
            continue
        review = _validated_completed_review(record, pair_input)
        if review is not None:
            reviews_by_pair[pair_id][str(review["model"])] = review
    return reviews_by_pair


def _review_record_matches_pair(
    record: Mapping[str, object], pair_input: Mapping[str, object]
) -> str:
    model = _normalized(record.get("model"))
    if (
        model not in PAIR_REVIEW_MODELS
        or _text(record.get("pair_id")) != _text(pair_input.get("pair_id"))
        or _text(record.get("input_fingerprint"))
        != _text(pair_input.get("input_fingerprint"))
        or _text(record.get("policy_version")) != _text(pair_input.get("policy_version"))
    ):
        return ""
    return model


def _reviewer_statuses_by_pair(
    pair_inputs_by_id: Mapping[str, Mapping[str, object]],
    review_records: Sequence[Mapping[str, object]],
    latest_reviews: Mapping[str, Mapping[str, Mapping[str, object]]],
) -> dict[str, list[dict[str, object]]]:
    latest_raw_records: dict[str, dict[str, Mapping[str, object]]] = defaultdict(dict)
    for record in review_records:
        pair_id = _text(record.get("pair_id"))
        pair_input = pair_inputs_by_id.get(pair_id)
        if pair_input is None:
            continue
        if (model := _review_record_matches_pair(record, pair_input)):
            latest_raw_records[pair_id][model] = record

    statuses_by_pair: dict[str, list[dict[str, object]]] = {}
    for pair_id, pair_input in pair_inputs_by_id.items():
        status_rows: list[dict[str, object]] = []
        for model in PAIR_REVIEW_MODELS:
            if model in latest_reviews.get(pair_id, {}):
                status_rows.append({"model": model, "status": "completed"})
                continue
            record = latest_raw_records.get(pair_id, {}).get(model)
            if record is None:
                status_rows.append({"model": model, "status": "not_run"})
                continue
            if (
                _text(record.get("review_policy_version"))
                != PAIR_REVIEWER_POLICY_VERSION
            ):
                status_rows.append({"model": model, "status": "stale_reviewer_policy"})
                continue
            status = _normalized(record.get("status"))
            if status == "completed":
                status_rows.append({"model": model, "status": "invalid_completed_record"})
                continue
            status_row: dict[str, object] = {"model": model, "status": status or "invalid_record"}
            error_code = _text(record.get("error_code"))
            error = _text(record.get("error"))
            if error_code:
                status_row["error_code"] = error_code
            if error:
                status_row["error"] = error
            status_rows.append(status_row)
        statuses_by_pair[pair_id] = status_rows
    return statuses_by_pair


def _member_ticket_id(pair_input: Mapping[str, object]) -> str:
    representative_ticket_id = _text(pair_input.get("representative_ticket_id"))
    left_ticket_id = _text(pair_input.get("left_ticket_id"))
    right_ticket_id = _text(pair_input.get("right_ticket_id"))
    if representative_ticket_id == left_ticket_id:
        return right_ticket_id
    if representative_ticket_id == right_ticket_id:
        return left_ticket_id
    raise ValueError(
        "Phase 09 pair input representative_ticket_id must be one side of its pair"
    )


def _numeric_risk_score(ticket: Mapping[str, object]) -> float:
    try:
        value = float(ticket.get("risk_score"))
    except (TypeError, ValueError):
        return -1.0
    return value if isfinite(value) else -1.0


def _ticket_risk_flags(ticket: Mapping[str, object]) -> list[str]:
    flags = ticket.get("risk_flags")
    if not isinstance(flags, list):
        return []
    return [_clean_text(flag) for flag in flags if _clean_text(flag)]


def _has_follow_up_evidence(ticket: Mapping[str, object]) -> bool:
    value = ticket.get("has_follow_up_evidence")
    if isinstance(value, bool):
        return value
    return _normalized(value) in {"yes", "true", "1", "是"}


def _ticket_follow_up_evidence(ticket: Mapping[str, object]) -> dict[str, object]:
    evidence: dict[str, object] = {
        "ticket_id": _ticket_id(ticket.get("ticket_id")),
        "has_follow_up_evidence": _has_follow_up_evidence(ticket),
        "follow_up_status": _clean_text(ticket.get("follow_up_status"))
        or "Not recorded",
    }
    for field_name in (
        "repair_plan_status",
        "target_version_status",
        "target_version_value",
        "planning_window_status",
        "planning_window_value",
        "commercial_solution_date_status",
        "commercial_solution_date_value",
        "reprel_change_status",
        "related_resolution_status",
        "postponement_document_status",
    ):
        value = _clean_text(ticket.get(field_name))
        if value:
            evidence[field_name] = value
    return evidence


def _formal_parent_id(pair_input: Mapping[str, object]) -> str:
    for evidence in pair_input.get("evidence", []):
        if not isinstance(evidence, Mapping):
            continue
        evidence_id = _text(evidence.get("evidence_id"))
        if evidence_id.startswith("formal_parent:"):
            return evidence_id.partition(":")[2]
    return ""


def build_confirmed_duplicate_groups(
    tickets: Sequence[Mapping[str, object]],
    pair_inputs: Sequence[Mapping[str, object]],
    review_records: Sequence[Mapping[str, object]],
) -> dict[str, list[dict[str, object]]]:
    """Build only representative-anchored groups confirmed independently by both models."""
    tickets_by_id: dict[str, Mapping[str, object]] = {}
    for ticket in tickets:
        ticket_id = _ticket_id(ticket.get("ticket_id"))
        if not ticket_id or ticket_id in tickets_by_id:
            raise ValueError("Phase 09 normalization groups require unique non-empty ticket IDs")
        tickets_by_id[ticket_id] = ticket

    pair_inputs_by_id: dict[str, Mapping[str, object]] = {}
    for pair_input in pair_inputs:
        pair_id = _text(pair_input.get("pair_id"))
        if not pair_id or pair_id in pair_inputs_by_id:
            raise ValueError("Phase 09 normalization groups require unique non-empty pair IDs")
        pair_inputs_by_id[pair_id] = pair_input
    latest_reviews = _latest_reviews_by_pair(pair_inputs_by_id, review_records)
    reviewer_statuses = _reviewer_statuses_by_pair(
        pair_inputs_by_id, review_records, latest_reviews
    )

    confirmed_pairs: list[dict[str, object]] = []
    quarantined_pairs: list[dict[str, object]] = []
    representative_by_member: dict[str, str] = {}
    for pair_id in sorted(pair_inputs_by_id):
        pair_input = pair_inputs_by_id[pair_id]
        representative_ticket_id = _text(pair_input.get("representative_ticket_id"))
        member_ticket_id = _member_ticket_id(pair_input)
        if (
            representative_ticket_id not in tickets_by_id
            or member_ticket_id not in tickets_by_id
        ):
            raise ValueError(f"Phase 09 pair {pair_id} references a ticket outside the input scope")
        pair_reviews = latest_reviews.get(pair_id, {})
        approved_reviews = [
            pair_reviews.get(model) for model in PAIR_REVIEW_MODELS
        ]
        is_confirmed = all(
            review is not None and review["verdict"] == "SAME_UNDERLYING_DEFECT"
            for review in approved_reviews
        )
        pair_result = {
            "pair_id": pair_id,
            "input_fingerprint": _text(pair_input.get("input_fingerprint")),
            "policy_version": _text(pair_input.get("policy_version")),
            "review_policy_version": PAIR_REVIEWER_POLICY_VERSION,
            "representative_ticket_id": representative_ticket_id,
            "member_ticket_id": member_ticket_id,
            "formal_parent_id": _formal_parent_id(pair_input),
            "required_evidence_ids": list(pair_input.get("required_evidence_ids", [])),
            "reviewer_results": [
                pair_reviews[model] for model in PAIR_REVIEW_MODELS if model in pair_reviews
            ],
            "reviewer_statuses": reviewer_statuses[pair_id],
        }
        if is_confirmed:
            if member_ticket_id in representative_by_member:
                raise ValueError(
                    f"Phase 09 ticket {member_ticket_id} is confirmed against more than one representative"
                )
            representative_by_member[member_ticket_id] = representative_ticket_id
            confirmed_pairs.append({**pair_result, "decision": "DUAL_CONFIRMED_SAME"})
        else:
            reason = (
                "reviewer_non_confirmation"
                if len(pair_reviews) == len(PAIR_REVIEW_MODELS)
                else "missing_or_invalid_reviewer_result"
            )
            quarantined_pairs.append(
                {**pair_result, "reason": reason}
            )

    members_by_representative: dict[str, list[str]] = defaultdict(list)
    for ticket_id in tickets_by_id:
        members_by_representative[representative_by_member.get(ticket_id, ticket_id)].append(
            ticket_id
        )

    groups: list[dict[str, object]] = []
    member_map: list[dict[str, object]] = []
    confirmed_pairs_by_representative: dict[str, list[dict[str, object]]] = defaultdict(list)
    for pair in confirmed_pairs:
        confirmed_pairs_by_representative[str(pair["representative_ticket_id"])].append(pair)
    for representative_ticket_id in _ordered_ticket_ids(list(members_by_representative)):
        member_ticket_ids = _ordered_ticket_ids(
            members_by_representative[representative_ticket_id]
        )
        member_tickets = [tickets_by_id[ticket_id] for ticket_id in member_ticket_ids]
        worst_risk_ticket = max(member_tickets, key=_numeric_risk_score)
        disposition_counts: dict[str, int] = defaultdict(int)
        risk_flags: list[str] = []
        for member_ticket in member_tickets:
            disposition_counts[
                _clean_text(member_ticket.get("closure_disposition")) or "Not recorded"
            ] += 1
            for flag in _ticket_risk_flags(member_ticket):
                if flag not in risk_flags:
                    risk_flags.append(flag)
        group_id = f"group-{representative_ticket_id}"
        groups.append(
            {
                "group_id": group_id,
                "representative_ticket_id": representative_ticket_id,
                "representative_title": _clean_text(
                    tickets_by_id[representative_ticket_id].get("name")
                )
                or "Not recorded",
                "function_context": _function_context(
                    tickets_by_id[representative_ticket_id]
                )
                or "Not recorded",
                "assigned_ecu": _clean_text(
                    tickets_by_id[representative_ticket_id].get("assigned_ecu")
                )
                or "Not recorded",
                "normalization_status": (
                    "confirmed_duplicate_group" if len(member_ticket_ids) > 1 else "unmerged_singleton"
                ),
                "member_ticket_ids": member_ticket_ids,
                "member_count": len(member_ticket_ids),
                "merged_member_count": max(0, len(member_ticket_ids) - 1),
                "worst_risk_score": worst_risk_ticket.get("risk_score"),
                "worst_risk_tier": _clean_text(worst_risk_ticket.get("risk_tier"))
                or "Not recorded",
                "risk_flags": risk_flags,
                "closure_disposition_counts": dict(sorted(disposition_counts.items())),
                "has_any_follow_up_evidence": any(
                    _has_follow_up_evidence(ticket) for ticket in member_tickets
                ),
                "follow_up_evidence": [
                    _ticket_follow_up_evidence(ticket) for ticket in member_tickets
                ],
                "formal_parent_ids": sorted(
                    {
                        _text(pair["formal_parent_id"])
                        for pair in confirmed_pairs_by_representative[
                            representative_ticket_id
                        ]
                        if _text(pair["formal_parent_id"])
                    }
                ),
            }
        )
        for ticket_id in member_ticket_ids:
            ticket = tickets_by_id[ticket_id]
            member_map.append(
                {
                    "group_id": group_id,
                    "ticket_id": ticket_id,
                    "representative_ticket_id": representative_ticket_id,
                    "is_representative": ticket_id == representative_ticket_id,
                    "is_confirmed_member": ticket_id in representative_by_member,
                    "closure_disposition": _clean_text(ticket.get("closure_disposition"))
                    or "Not recorded",
                    "risk_score": ticket.get("risk_score"),
                    "risk_tier": _clean_text(ticket.get("risk_tier")) or "Not recorded",
                    "has_follow_up_evidence": _has_follow_up_evidence(ticket),
                    "follow_up_status": _clean_text(ticket.get("follow_up_status"))
                    or "Not recorded",
                }
            )

    return {
        "groups": groups,
        "member_map": member_map,
        "confirmed_pairs": confirmed_pairs,
        "quarantined_pairs": quarantined_pairs,
    }


def write_frozen_pair_review_inputs(
    *,
    payload: Mapping[str, object],
    run_directory: str | Path,
    generated_at: str | None = None,
) -> dict[str, object]:
    """Freeze the selected tickets and strict pair inputs before model review."""
    raw_tickets = payload.get("tickets")
    if not isinstance(raw_tickets, list):
        raise ValueError("Phase 09 frozen pair review payload requires a tickets array")
    tickets = [
        dict(ticket)
        for ticket in raw_tickets
        if isinstance(ticket, Mapping)
        if ticket.get("closure_disposition") in TARGET_CLOSURE_DISPOSITIONS
    ]
    pair_inputs = build_pair_review_input_records(tickets)
    source = payload.get("source")
    timestamp = generated_at or datetime.now(timezone.utc).isoformat()
    snapshot = {
        "schema_version": "phase09-duplicate-normalization-source-snapshot-v1",
        "run_id": Path(run_directory).name,
        "generated_at": timestamp,
        "source": dict(source) if isinstance(source, Mapping) else {},
        "target_closure_dispositions": sorted(TARGET_CLOSURE_DISPOSITIONS),
        "tickets": tickets,
    }
    snapshot_bytes = json.dumps(
        snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    pair_input_bytes = "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        for record in pair_inputs
    ).encode("utf-8")
    output_directory = Path(run_directory)
    output_directory.mkdir(parents=True, exist_ok=False)
    (output_directory / "source_snapshot.json").write_bytes(snapshot_bytes)
    (output_directory / "pair_inputs.jsonl").write_bytes(pair_input_bytes)
    manifest: dict[str, object] = {
        "schema_version": "phase09-duplicate-normalization-source-manifest-v1",
        "run_id": output_directory.name,
        "generated_at": timestamp,
        "source": snapshot["source"],
        "target_ticket_count": len(tickets),
        "pair_input_count": len(pair_inputs),
        "source_snapshot_sha256": sha256(snapshot_bytes).hexdigest(),
        "pair_inputs_sha256": sha256(pair_input_bytes).hexdigest(),
        "pair_inputs_hash_basis": "raw UTF-8 file bytes",
    }
    (output_directory / "source_manifest.json").write_bytes(
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )
    return manifest