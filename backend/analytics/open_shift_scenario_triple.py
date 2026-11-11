from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import Mapping, Sequence

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from backend.analytics.open_ticket_postponement import (
    load_open_ticket_records,
    load_validated_llm_summaries,
    parse_release,
    release_index,
    select_open_shift_ticket_records,
    shift_confidence_category,
)
from backend.analytics.phase09_all_function_after_sales_catalog import (
    render_all_function_catalog_html,
)
from backend.analytics.phase09_idcevo_scenario_build import (
    PROBLEM_TYPE_LABELS,
    PROBLEM_TYPE_ORDER,
    SERVICE_FUNCTION_CATALOG,
    SYMPTOM_PATTERN_LABELS,
    TRIGGER_PATTERN_LABELS,
    build_symptom_solution_trees,
    classify_problem_type,
    classify_service_function,
    extract_symptom_keyword,
    extract_trigger_keyword,
    render_symptom_solution_tree_catalog_html,
)


WHITESPACE_PATTERN = re.compile(r"\s+")
PUNCTUATION_PATTERN = re.compile(r"[，。！？、；：“”‘’（）()【】\[\]{},.!?;:\"'\\/]")
SIMILARITY_TOKEN_PATTERN = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]{2,}", re.IGNORECASE)
FUTURE_RESOLUTION_STATUS = "Recorded future resolution plan - not verified repair"
EXCEL_EVIDENCE_CHUNK_SIZE = 30_000
RECURRENCE_DIMENSION_FIELDS = (
    "solution_cluster",
    "product_areas",
    "defect_category",
)
RECURRENCE_TARGET_FIELDS = (
    "target_i_step",
    "target_release",
    "shift_to_set_pu",
    "first_use_sop_pu",
    "closed_in_version",
    "sw_hrl_closed_version",
    "app_version",
)
RECURRENCE_STOPWORDS = {
    "a", "after", "an", "and", "are", "at", "by", "for", "from", "in",
    "is", "issue", "of", "on", "or", "the", "to", "with",
}
RECURRENCE_SIMILARITY_THRESHOLD = 0.55


def _text(value: object) -> str:
    return str(value or "").strip()


def _safe_excel_value(value: object) -> object:
    if not isinstance(value, str):
        return value
    return "'" + value if value[:1] in ("=", "+", "-", "@") else value


def _evidence_chunks(value: object) -> list[str]:
    text = _text(value)
    if not text:
        return [""]
    return [
        text[index : index + EXCEL_EVIDENCE_CHUNK_SIZE]
        for index in range(0, len(text), EXCEL_EVIDENCE_CHUNK_SIZE)
    ]


def _normalized_symptom(value: object) -> str:
    return WHITESPACE_PATTERN.sub(
        " ",
        PUNCTUATION_PATTERN.sub(" ", _text(value).casefold()),
    ).strip()


def _future_resolution_plan(record: Mapping[str, object]) -> str:
    category, basis = shift_confidence_category(record)
    evidence = [("Confidence category", category), ("Confidence basis", basis)]
    for label, key in (
        ("Target I-Step", "target_i_step"),
        ("Target PU/I-Step", "target_release"),
        ("Shift-to-Set PU", "shift_to_set_pu"),
        ("First Use/SOP PU", "first_use_sop_pu"),
        ("Closed in version", "closed_in_version"),
        ("SW-HRL closed version", "sw_hrl_closed_version"),
        ("App version", "app_version"),
        ("Supplier confirmed I-Step", "supplier_confirmed_i_step"),
        ("Predicted target I-Step", "predicted_target_i_step"),
        ("Target week", "target_week"),
    ):
        value = _text(record.get(key))
        if value:
            evidence.append((label, value))
    return "\n".join("{}: {}".format(label, value) for label, value in evidence)


def build_shift_full_records(
    records: Sequence[Mapping[str, object]],
    summaries_by_ticket_id: Mapping[str, Mapping[str, object]],
    metadata_by_ticket_id: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]:
    full_records: list[dict[str, object]] = []
    for record in records:
        ticket_id = _text(record.get("ticket_id"))
        summary = summaries_by_ticket_id.get(ticket_id)
        if not summary or _text(summary.get("source")).casefold() != "llm":
            continue
        metadata = metadata_by_ticket_id.get(ticket_id, {})
        issue_summary = _text(summary.get("issue_summary"))
        raw_function = (
            _text(metadata.get("solution_cluster"))
            or _text(metadata.get("product_areas"))
            or _text(metadata.get("defect_category"))
        )
        service = classify_service_function(
            {
                "rep_function": raw_function,
                "function": raw_function,
                "function_responsible": _text(
                    metadata.get("function_responsible")
                    or record.get("function_responsible")
                ),
                "affected_function": raw_function,
                "rep_title": _text(record.get("name")),
                "customer_symptom": issue_summary,
                "trigger_condition": "",
            }
        )
        confidence_category, confidence_basis = shift_confidence_category(record)
        full_records.append(
            {
                "ticket_id": ticket_id,
                "title": _text(record.get("name")),
                "phase": _text(record.get("phase")),
                "service_function_id": _text(service.get("service_function_id")),
                "service_function": _text(service.get("service_function")),
                "service_mapping_source": _text(service.get("match_source")),
                "service_domain": _text(service.get("service_domain")),
                "confidence_category": confidence_category,
                "confidence_basis": confidence_basis,
                "issue_summary": issue_summary,
                "postponement_assessment": _text(
                    summary.get("postponement_assessment")
                ),
                "postponement_reason_analysis": _text(
                    summary.get("postponement_reason_analysis")
                ),
                "resolution_timing_analysis": _text(
                    summary.get("resolution_timing_analysis")
                ),
                "resolution_version_analysis": _text(
                    summary.get("resolution_version_analysis")
                ),
                "review_focus": _text(summary.get("review_focus")),
                "future_resolution_status": FUTURE_RESOLUTION_STATUS,
                "future_resolution_plan": _future_resolution_plan(record),
                "source_software_version": _text(record.get("software_version")),
                "source_release": _text(record.get("reported_release")),
                "target_i_step": _text(record.get("target_i_step")),
                "target_release": _text(record.get("target_release")),
                "shift_to_set_pu": _text(record.get("shift_to_set_pu")),
                "first_use_sop_pu": _text(record.get("first_use_sop_pu")),
                "closed_in_version": _text(record.get("closed_in_version")),
                "sw_hrl_closed_version": _text(record.get("sw_hrl_closed_version")),
                "app_version": _text(record.get("app_version")),
                "function_responsible": _text(
                    metadata.get("function_responsible")
                    or record.get("function_responsible")
                ),
                "solution_cluster": _text(metadata.get("solution_cluster")),
                "product_areas": _text(metadata.get("product_areas")),
                "defect_category": _text(metadata.get("defect_category")),
                "assigned_ecu": _text(metadata.get("assigned_ecu")),
                "llm_source": _text(summary.get("source")),
                "llm_model": _text(summary.get("model")),
                "description": _text(record.get("description")),
                "comments": _text(record.get("comments")),
                "plan_evidence_signals": _text(record.get("plan_evidence_signals")),
            }
        )
    return sorted(full_records, key=lambda item: _text(item.get("ticket_id")))


def _read_jsonl_records(path: str | Path) -> list[dict[str, object]]:
    resolved_path = Path(path)
    records: list[dict[str, object]] = []
    for line_number, line in enumerate(
        resolved_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(
                "Invalid JSONL at {}:{}".format(resolved_path, line_number)
            ) from error
        if isinstance(value, dict):
            records.append(value)
    return records


def load_shift_full_records_from_source_workbook(
    source_path: str | Path,
) -> list[dict[str, object]]:
    """Restore structured Open Shift records from a generated FullLLM workbook."""
    workbook = load_workbook(source_path, read_only=True, data_only=True)
    try:
        if "06_全部明细" not in workbook.sheetnames:
            raise ValueError("Missing 06_全部明细 in {}".format(source_path))
        sheet = workbook["06_全部明细"]
        rows = sheet.iter_rows(values_only=True)
        try:
            headers = [_text(value) for value in next(rows)]
        except StopIteration as error:
            raise ValueError("Missing detail headers in {}".format(source_path)) from error
        service_by_name = {
            _text(service.get("service_function")).casefold(): service
            for service in SERVICE_FUNCTION_CATALOG
        }
        records: list[dict[str, object]] = []
        for row in rows:
            values = {
                header: row[index] if index < len(row) else ""
                for index, header in enumerate(headers)
                if header
            }
            ticket_id = _text(values.get("Ticket ID"))
            if not ticket_id:
                continue
            phase = _text(values.get("Phase"))
            service_function = _text(values.get("Service Function"))
            service = service_by_name.get(service_function.casefold(), {})
            records.append(
                {
                    "ticket_id": ticket_id,
                    "title": _text(values.get("Ticket 标题")),
                    "phase": phase,
                    "ticket_lifecycle_status": _text(
                        values.get("Ticket Lifecycle Status")
                    )
                    or _ticket_lifecycle_status({"phase": phase}),
                    "selection_scope": _text(values.get("Selection Scope"))
                    or "Open Shift",
                    "service_function_id": _text(
                        service.get("service_function_id")
                    ),
                    "service_function": service_function,
                    "service_mapping_source": _text(
                        values.get("Service Mapping Source")
                    ),
                    "service_domain": _text(service.get("service_domain")),
                    "confidence_category": _text(
                        values.get("Confidence Category")
                    ),
                    "confidence_basis": _text(values.get("Confidence Basis")),
                    "source_software_version": _text(
                        values.get("Source Software")
                    ),
                    "source_release": _text(values.get("Source PU/I-Step")),
                    "target_i_step": _text(values.get("Target I-Step")),
                    "target_release": _text(values.get("Target PU/I-Step")),
                    "shift_to_set_pu": _text(values.get("Shift-to-Set PU")),
                    "first_use_sop_pu": _text(values.get("First Use/SOP PU")),
                    "closed_in_version": _text(values.get("Closed in Version")),
                    "sw_hrl_closed_version": _text(
                        values.get("SW-HRL Closed Version")
                    ),
                    "app_version": _text(values.get("App Version")),
                    "future_resolution_status": _text(
                        values.get("Future Resolution Status")
                    )
                    or FUTURE_RESOLUTION_STATUS,
                    "future_resolution_plan": _text(
                        values.get("Future Resolution Plan")
                    ),
                    "issue_summary": _text(values.get("LLM 问题摘要")),
                    "postponement_reason_analysis": _text(
                        values.get("LLM 延期原因分析")
                    ),
                    "resolution_timing_analysis": _text(
                        values.get("LLM 解决时间分析")
                    ),
                    "resolution_version_analysis": _text(
                        values.get("LLM 解决版本分析")
                    ),
                    "postponement_assessment": _text(
                        values.get("LLM 延期/计划判断")
                    ),
                    "review_focus": _text(values.get("LLM 待核实")),
                    "function_responsible": _text(
                        values.get("Function Responsible")
                    ),
                    "solution_cluster": _text(values.get("Solution Cluster")),
                    "product_areas": _text(values.get("Product Areas")),
                    "defect_category": _text(values.get("Defect Category")),
                    "assigned_ecu": _text(values.get("Assigned ECU")),
                    "llm_source": _text(values.get("LLM Source")),
                    "llm_model": _text(values.get("LLM Model")),
                    "process_tier": _text(values.get("Process Tier")),
                    "process_basis": _text(values.get("Process Basis")),
                    "description": "",
                    "comments": "",
                    "plan_evidence_signals": _text(
                        values.get("Future Resolution Plan")
                    ),
                }
            )
        return sorted(records, key=lambda record: _text(record.get("ticket_id")))
    finally:
        workbook.close()


def load_ticket_creation_times(
    db_path: str | Path,
    ticket_ids: Sequence[str],
) -> dict[str, str]:
    if not ticket_ids:
        return {}
    connection = sqlite3.connect(str(db_path))
    try:
        timestamps: dict[str, str] = {}
        for start_index in range(0, len(ticket_ids), 900):
            batch = ticket_ids[start_index : start_index + 900]
            placeholders = ",".join("?" for _ticket_id in batch)
            rows = connection.execute(
                "SELECT defect_id, creation_time FROM octane_defects "
                "WHERE defect_id IN ({})".format(placeholders),
                tuple(batch),
            )
            timestamps.update({_text(row[0]): _text(row[1]) for row in rows})
        return timestamps
    finally:
        connection.close()


def _load_function_metadata(
    db_path: str | Path,
    ticket_ids: Sequence[str],
) -> dict[str, dict[str, object]]:
    if not ticket_ids:
        return {}
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    try:
        columns = {
            _text(row[1])
            for row in connection.execute("PRAGMA table_info(octane_defects)")
        }
        if "defect_id" not in columns:
            raise ValueError("octane_defects must contain defect_id")

        def column_or_empty(name: str) -> str:
            return name if name in columns else "''"

        metadata: dict[str, dict[str, object]] = {}
        for start_index in range(0, len(ticket_ids), 900):
            batch = ticket_ids[start_index : start_index + 900]
            placeholders = ",".join("?" for _ in batch)
            rows = connection.execute(
                """
                SELECT defect_id,
                       {solution_cluster} AS solution_cluster,
                       {product_areas} AS product_areas,
                       {defect_category} AS defect_category,
                       {assigned_ecu} AS assigned_ecu,
                       {function_responsible} AS function_responsible
                FROM octane_defects
                WHERE defect_id IN ({placeholders})
                """.format(
                    solution_cluster=column_or_empty("solution_cluster"),
                    product_areas=column_or_empty("product_areas"),
                    defect_category=column_or_empty("defect_category"),
                    assigned_ecu=column_or_empty("assigned_ecu"),
                    function_responsible=column_or_empty("function_responsible"),
                    placeholders=placeholders,
                ),
                tuple(batch),
            ).fetchall()
            for row in rows:
                metadata[_text(row["defect_id"])] = dict(row)
        return metadata
    finally:
        connection.close()


def _comment_items(value: object) -> list[dict[str, str]]:
    try:
        parsed = json.loads(_text(value) or "[]")
    except json.JSONDecodeError:
        return [{"timestamp": "", "actor": "", "content": _text(value)}] if _text(value) else []
    if not isinstance(parsed, list):
        return []
    items: list[dict[str, str]] = []
    for item in parsed:
        if not isinstance(item, Mapping):
            content = _text(item)
            if content:
                items.append({"timestamp": "", "actor": "", "content": content})
            continue
        author = item.get("author")
        actor = ""
        if isinstance(author, Mapping):
            actor = _text(author.get("full_name") or author.get("name"))
        else:
            actor = _text(author)
        content = _text(item.get("text") or item.get("content") or item.get("comment"))
        if content:
            items.append(
                {
                    "timestamp": _text(
                        item.get("creation_time")
                        or item.get("timestamp")
                        or item.get("last_modified")
                    ),
                    "actor": actor,
                    "content": content,
                }
            )
    return items


def load_open_shift_raw_evidence(
    db_path: str | Path,
    ticket_ids: Sequence[str],
) -> list[dict[str, object]]:
    if not ticket_ids:
        return []
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    try:
        defect_columns = {
            _text(row[1])
            for row in connection.execute("PRAGMA table_info(octane_defects)")
        }
        history_columns = {
            _text(row[1])
            for row in connection.execute("PRAGMA table_info(octane_defect_history_events)")
        }

        def defect_column(name: str) -> str:
            return name if name in defect_columns else "''"

        def history_column(name: str) -> str:
            return "history." + name if name in history_columns else "''"

        defect_rows: list[sqlite3.Row] = []
        history_rows: list[sqlite3.Row] = []
        for start_index in range(0, len(ticket_ids), 900):
            batch = ticket_ids[start_index : start_index + 900]
            placeholders = ",".join("?" for _ in batch)
            defect_rows.extend(
                connection.execute(
                    """
                    SELECT defect_id,
                           {description} AS description,
                           {comments} AS comments
                    FROM octane_defects
                    WHERE defect_id IN ({placeholders})
                    ORDER BY defect_id
                    """.format(
                        description=defect_column("description"),
                        comments=defect_column("comments"),
                        placeholders=placeholders,
                    ),
                    tuple(batch),
                ).fetchall()
            )
            history_rows.extend(
                connection.execute(
                    """
                    SELECT history.defect_id,
                           {event_timestamp} AS event_timestamp,
                           {user_name} AS user_name,
                           history.field_name,
                           {old_value} AS old_value,
                           {new_value} AS new_value,
                           {old_value_text} AS old_value_text,
                           {new_value_text} AS new_value_text,
                           {entry_index} AS entry_index,
                           {change_index} AS change_index
                    FROM octane_defect_history_events AS history
                    WHERE history.defect_id IN ({placeholders})
                    ORDER BY history.defect_id, {event_timestamp}, {entry_index}, {change_index}
                    """.format(
                        event_timestamp=history_column("event_timestamp"),
                        user_name=history_column("user_name"),
                        old_value=history_column("old_value"),
                        new_value=history_column("new_value"),
                        old_value_text=history_column("old_value_text"),
                        new_value_text=history_column("new_value_text"),
                        entry_index=history_column("entry_index"),
                        change_index=history_column("change_index"),
                        placeholders=placeholders,
                    ),
                    tuple(batch),
                ).fetchall()
                    )
        evidence: list[dict[str, object]] = []
        for row in sorted(defect_rows, key=lambda item: _text(item["defect_id"])):
            ticket_id = _text(row["defect_id"])
            description = _text(row["description"])
            if description:
                evidence.append(
                    {
                        "ticket_id": ticket_id,
                        "evidence_type": "Description",
                        "timestamp": "",
                        "actor": "",
                        "field_name": "description",
                        "old_value": "",
                        "new_value": "",
                        "content": description,
                    }
                )
            for comment in _comment_items(row["comments"]):
                evidence.append(
                    {
                        "ticket_id": ticket_id,
                        "evidence_type": "Comment",
                        "timestamp": comment["timestamp"],
                        "actor": comment["actor"],
                        "field_name": "comments",
                        "old_value": "",
                        "new_value": "",
                        "content": comment["content"],
                    }
                )
        for row in history_rows:
            evidence.append(
                {
                    "ticket_id": _text(row["defect_id"]),
                    "evidence_type": "History",
                    "timestamp": _text(row["event_timestamp"]),
                    "actor": _text(row["user_name"]),
                    "field_name": _text(row["field_name"]),
                    "old_value": _text(row["old_value_text"])
                    or _text(row["old_value"]),
                    "new_value": _text(row["new_value_text"])
                    or _text(row["new_value"]),
                    "content": "",
                }
            )
        return evidence
    finally:
        connection.close()


def load_open_shift_full_records(
    db_path: str | Path,
    inputs_path: str | Path,
    summaries_path: str | Path,
) -> list[dict[str, object]]:
    candidate_records = select_open_shift_ticket_records(
        load_open_ticket_records(db_path)
    )
    input_records = _read_jsonl_records(inputs_path)
    summaries = load_validated_llm_summaries(input_records, summaries_path)
    candidate_ids = [_text(record.get("ticket_id")) for record in candidate_records]
    metadata = _load_function_metadata(db_path, candidate_ids)
    return build_shift_full_records(candidate_records, summaries, metadata)


def build_shift_scenarios(
    records: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    clusters: dict[tuple[str, str, str], list[Mapping[str, object]]] = defaultdict(list)
    for record in records:
        service_function_id = _text(record.get("service_function_id")) or "CDUNSPECIFIED"
        symptom = _text(record.get("issue_summary")) or _text(record.get("title"))
        symptom_token = extract_symptom_keyword(symptom)
        trigger_token = extract_trigger_keyword(
            "{} {}".format(symptom, _text(record.get("title")))
        )
        clusters[(service_function_id, symptom_token, trigger_token)].append(record)

    scenarios: list[dict[str, object]] = []
    for index, ((service_function_id, symptom_token, trigger_token), members) in enumerate(
        sorted(clusters.items()),
        start=1,
    ):
        ordered_members = sorted(members, key=lambda item: _text(item.get("ticket_id")))
        representative = ordered_members[0]
        problem_type = classify_problem_type(
            {
                "customer_symptom": _text(representative.get("issue_summary")),
                "trigger_condition": trigger_token,
                "observed_behaviour": _text(representative.get("issue_summary")),
                "expected_behaviour": "",
                "rep_title": _text(representative.get("title")),
            }
        )
        plans = [
            _text(member.get("future_resolution_plan"))
            for member in ordered_members
            if _text(member.get("future_resolution_plan"))
        ]
        future_plans = list(dict.fromkeys(plans))
        postponement_reasons = list(
            dict.fromkeys(
                _text(member.get("postponement_reason_analysis"))
                for member in ordered_members
                if _text(member.get("postponement_reason_analysis"))
            )
        )
        resolution_timings = list(
            dict.fromkeys(
                _text(member.get("resolution_timing_analysis"))
                for member in ordered_members
                if _text(member.get("resolution_timing_analysis"))
            )
        )
        resolution_versions = list(
            dict.fromkeys(
                _text(member.get("resolution_version_analysis"))
                for member in ordered_members
                if _text(member.get("resolution_version_analysis"))
            )
        )
        owners = Counter(
            _text(member.get("function_responsible"))
            for member in ordered_members
            if _text(member.get("function_responsible"))
        )
        categories = Counter(
            _text(member.get("confidence_category"))
            for member in ordered_members
            if _text(member.get("confidence_category"))
        )
        scenarios.append(
            {
                "scenario_id": "SHIFT-SC-{:04d}".format(index),
                "service_function_id": service_function_id,
                "service_function": _text(representative.get("service_function"))
                or "ConnectedDrive / Unspecified",
                "service_mapping_source": _text(
                    representative.get("service_mapping_source")
                ),
                "symptom_token": symptom_token,
                "trigger_token": trigger_token,
                "problem_type": problem_type,
                "customer_symptom": _text(representative.get("issue_summary"))
                or _text(representative.get("title")),
                "recorded_ticket_context": _text(representative.get("title")),
                "future_resolution_status": FUTURE_RESOLUTION_STATUS,
                "future_resolution_plans": future_plans,
                "postponement_reason_analyses": postponement_reasons,
                "resolution_timing_analyses": resolution_timings,
                "resolution_version_analyses": resolution_versions,
                "confidence_categories": [
                    category for category, _count in categories.most_common()
                ],
                "ticket_count": len(ordered_members),
                "ticket_ids": [
                    _text(member.get("ticket_id")) for member in ordered_members
                ],
                "rep_ticket_id": _text(representative.get("ticket_id")),
                "rep_title": _text(representative.get("title")),
                "function_responsibles": [
                    owner for owner, _count in owners.most_common()
                ],
                "source_software_versions": list(
                    dict.fromkeys(
                        _text(member.get("source_software_version"))
                        for member in ordered_members
                        if _text(member.get("source_software_version"))
                    )
                ),
            }
        )
    return scenarios


def _compact_detail(value: object, *, limit: int = 76) -> str:
    text = WHITESPACE_PATTERN.sub(" ", _text(value))
    if len(text) <= limit:
        return text
    return "{}...".format(text[: max(1, limit - 3)])


def _ticket_lifecycle_status(record: Mapping[str, object]) -> str:
    status = _text(record.get("ticket_lifecycle_status"))
    if status:
        return status
    phase = _text(record.get("phase"))
    if phase[:2] in {"06", "09"}:
        return "Closed / Phase 09"
    return "Open / active"


def _normalized_dimension_value(value: object) -> str:
    return WHITESPACE_PATTERN.sub(
        " ",
        PUNCTUATION_PATTERN.sub(" ", _text(value).casefold()),
    ).strip()


def _recurrence_tokens(value: object) -> set[str]:
    normalized = _normalized_dimension_value(value)
    tokens: set[str] = set()
    for token in SIMILARITY_TOKEN_PATTERN.findall(normalized):
        token = token.strip()
        if not token or token in RECURRENCE_STOPWORDS:
            continue
        tokens.add(token)
        if any("\u4e00" <= character <= "\u9fff" for character in token):
            tokens.update(
                token[index : index + 2]
                for index in range(max(0, len(token) - 1))
            )
    return tokens


def _recurrence_text(record: Mapping[str, object]) -> str:
    return " ".join(
        value
        for value in (
            _text(record.get("issue_summary")),
            _text(record.get("title") or record.get("name")),
        )
        if value
    )


def _recurrence_similarity(
    left_record: Mapping[str, object],
    right_record: Mapping[str, object],
) -> float:
    left_tokens = _recurrence_tokens(_recurrence_text(left_record))
    right_tokens = _recurrence_tokens(_recurrence_text(right_record))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _record_target_release(record: Mapping[str, object]) -> tuple[int, int] | None:
    for field_name in RECURRENCE_TARGET_FIELDS:
        release = parse_release(_text(record.get(field_name)))
        if release is not None:
            return release
    return None


def _record_dimension_keys(record: Mapping[str, object]) -> set[tuple[str, str]]:
    return {
        (field_name, normalized)
        for field_name in RECURRENCE_DIMENSION_FIELDS
        if (normalized := _normalized_dimension_value(record.get(field_name)))
    }


def assess_post_target_recurrence(
    records: Sequence[Mapping[str, object]],
    recurrence_corpus: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Assess recurrence at or after a recorded target without claiming repair."""
    candidates_by_dimension: dict[tuple[str, str], list[Mapping[str, object]]] = defaultdict(list)
    for candidate in recurrence_corpus:
        if parse_release(_text(candidate.get("software_version"))) is None:
            continue
        for dimension_key in _record_dimension_keys(candidate):
            candidates_by_dimension[dimension_key].append(candidate)

    assessed_records: list[dict[str, object]] = []
    for record in records:
        assessed = dict(record)
        target_release = _record_target_release(record)
        source_tokens = _recurrence_tokens(_recurrence_text(record))
        dimension_keys = _record_dimension_keys(record)
        if target_release is None:
            assessed.update(
                {
                    "resolution_verification_status": "comparison_unavailable",
                    "resolution_verification_summary": (
                        "Comparison unavailable: no parseable target release is "
                        "recorded."
                    ),
                    "post_target_recurrence_candidates": [],
                }
            )
            assessed_records.append(assessed)
            continue
        if not source_tokens or not dimension_keys:
            assessed.update(
                {
                    "resolution_verification_status": "comparison_unavailable",
                    "resolution_verification_summary": (
                        "Comparison unavailable: ticket symptom text or function "
                        "dimension is insufficient."
                    ),
                    "post_target_recurrence_candidates": [],
                }
            )
            assessed_records.append(assessed)
            continue

        source_ticket_id = _text(record.get("ticket_id"))
        dimension_candidate_groups: list[list[Mapping[str, object]]] = []
        for dimension_key in dimension_keys:
            candidates = []
            for candidate in candidates_by_dimension.get(dimension_key, []):
                candidate_ticket_id = _text(candidate.get("ticket_id"))
                if candidate_ticket_id and candidate_ticket_id != source_ticket_id:
                    candidates.append(candidate)
            if candidates:
                dimension_candidate_groups.append(candidates)
        candidate_by_id: dict[str, Mapping[str, object]] = {}
        if dimension_candidate_groups:
            for candidate in min(dimension_candidate_groups, key=len):
                candidate_by_id[_text(candidate.get("ticket_id"))] = candidate
        comparable_candidates: list[Mapping[str, object]] = []
        matching_candidates: list[tuple[float, Mapping[str, object]]] = []
        for candidate in candidate_by_id.values():
            candidate_release = parse_release(_text(candidate.get("software_version")))
            if candidate_release is None or release_index(candidate_release) < release_index(target_release):
                continue
            comparable_candidates.append(candidate)
            score = _recurrence_similarity(record, candidate)
            if score >= RECURRENCE_SIMILARITY_THRESHOLD:
                matching_candidates.append((score, candidate))

        matching_candidates.sort(
            key=lambda item: (
                -item[0],
                _text(item[1].get("ticket_id")),
            )
        )
        evidence = [
            {
                "ticket_id": _text(candidate.get("ticket_id")),
                "software_version": _text(candidate.get("software_version")),
                "phase": _text(candidate.get("phase")),
            }
            for _score, candidate in matching_candidates[:5]
        ]
        if evidence:
            assessed.update(
                {
                    "resolution_verification_status": "recurrence_observed",
                    "resolution_verification_summary": (
                        "Recurrence observed in {} target-or-later ticket(s); "
                        "resolution is not verified."
                    ).format(len(evidence)),
                    "post_target_recurrence_candidates": evidence,
                }
            )
        elif comparable_candidates:
            assessed.update(
                {
                    "resolution_verification_status": "no_recurrence_evidence",
                    "resolution_verification_summary": (
                        "No similar ticket was found in {} comparable target-or-later "
                        "ticket(s) in the current corpus; this is not a verified repair."
                    ).format(len(comparable_candidates)),
                    "post_target_recurrence_candidates": [],
                }
            )
        else:
            assessed.update(
                {
                    "resolution_verification_status": "comparison_unavailable",
                    "resolution_verification_summary": (
                        "Comparison unavailable: no same-dimension target-or-later "
                        "ticket is available in the current corpus."
                    ),
                    "post_target_recurrence_candidates": [],
                }
            )
        assessed_records.append(assessed)
    return assessed_records


def _resolution_plan_summary(scenario: Mapping[str, object]) -> str:
    if scenario.get("source_evidence_status") == "unavailable_preserved":
        return "Historical-only evidence: current resolution plan unavailable."
    if scenario.get("source_evidence_status") in {"current_verified", "database_snapshot"}:
        return "\n".join(_text(scenario.get(field)) for field in ("resolution_timing_analysis", "resolution_version_analysis") if _text(scenario.get(field))) or "No supported current resolution plan recorded."
    lines = [
        "{}: {}".format(label, _text(scenario.get(field_name)))
        for label, field_name in (
            ("Target I-Step", "target_i_step"),
            ("Target release", "target_release"),
            ("Shift-to-Set PU", "shift_to_set_pu"),
            ("First Use/SOP PU", "first_use_sop_pu"),
            ("Closed in version", "closed_in_version"),
            ("SW-HRL closed version", "sw_hrl_closed_version"),
            ("App version", "app_version"),
        )
        if _text(scenario.get(field_name))
    ]
    process_tier = _text(scenario.get("process_tier"))
    if process_tier:
        lines.append("Process tier: {}".format(_compact_detail(process_tier, limit=160)))
    return "\n".join(lines) or "No structured future-resolution plan recorded."


def _resolution_version_summary(scenario: Mapping[str, object]) -> str:
    if scenario.get("source_evidence_status") == "unavailable_preserved":
        return "Historical-only evidence: current resolution version unavailable."
    if scenario.get("source_evidence_status") in {"current_verified", "database_snapshot"}:
        return _text(scenario.get("resolution_version_analysis")) or "No supported current resolution version recorded."
    lines = [
        "{}: {}".format(label, _text(scenario.get(field_name)))
        for label, field_name in (
            ("Source software", "source_software_version"),
            ("Target I-Step", "target_i_step"),
            ("Target release", "target_release"),
            ("Closed in version", "closed_in_version"),
            ("SW-HRL closed version", "sw_hrl_closed_version"),
            ("App version", "app_version"),
        )
        if _text(scenario.get(field_name))
    ]
    return "\n".join(lines) or "No structured resolution version recorded."


def _root_cause_evidence_summary(scenario: Mapping[str, object]) -> str:
    root_cause = _text(scenario.get("rca_root_cause"))
    if not root_cause:
        return "No recorded root-cause evidence."
    return "Recorded RCA root cause: {}".format(_compact_detail(root_cause, limit=500))


def _recovery_evidence_summary(scenario: Mapping[str, object]) -> str:
    recovery = _text(scenario.get("rca_recovery"))
    if not recovery:
        return "No recorded solution or recovery evidence."
    return "Recorded recovery action: {}".format(_compact_detail(recovery, limit=500))


def _post_target_recurrence_evidence_text(
    scenario: Mapping[str, object],
) -> str:
    candidates = scenario.get("post_target_recurrence_candidates")
    if not isinstance(candidates, list) or not candidates:
        return ""
    return "\n".join(
        "{} | {} | {}".format(
            _text(candidate.get("ticket_id")),
            _text(candidate.get("software_version")),
            _text(candidate.get("phase")),
        )
        for candidate in candidates
        if isinstance(candidate, Mapping)
    )


def _raw_ticket_detail_groups(
    scenario: Mapping[str, object],
) -> list[dict[str, str]]:
    planning_lines = [
        value
        for value in (
            _text(scenario.get("future_resolution_plan")),
            _text(scenario.get("plan_evidence_signals")),
            _text(scenario.get("process_basis")),
        )
        if value
    ]
    llm_lines = [
        "{}:\n{}".format(label, value)
        for label, value in (
            ("LLM postponement reason analysis", _text(scenario.get("postponement_reason_analysis"))),
            ("LLM resolution timing analysis", _text(scenario.get("resolution_timing_analysis"))),
            ("LLM resolution version analysis", _text(scenario.get("resolution_version_analysis"))),
        )
        if value
    ]
    groups = []
    if planning_lines:
        groups.append(
            {
                "label": "Raw ticket planning evidence",
                "value": "\n---\n".join(dict.fromkeys(planning_lines)),
            }
        )
    if llm_lines:
        groups.append(
            {
                "label": "Full LLM planning context",
                "value": "\n\n".join(llm_lines),
            }
        )
    return groups


def _detailed_trigger_context(record: Mapping[str, object]) -> str:
    explicit_trigger = _text(record.get("trigger_condition"))
    if explicit_trigger:
        return explicit_trigger
    context_source = "{} {}".format(
        _text(record.get("issue_summary")),
        _text(record.get("title")),
    )
    trigger_token = extract_trigger_keyword(context_source)
    if trigger_token not in {"", "other", "unknown"}:
        return "Detected occurrence context: {}".format(
            trigger_token.replace("-", " ")
        )
    return "No separate trigger retained; ticket title: {}".format(
        _text(record.get("title")) or "Not recorded"
    )


def _ticket_detail_fields(
    scenario: Mapping[str, object],
) -> list[dict[str, str]]:
    values = (
        ("Ticket ID", _text(scenario.get("ticket_id"))),
        ("Ticket lifecycle", _text(scenario.get("ticket_lifecycle_status"))),
        ("Ticket phase", _text(scenario.get("phase"))),
        ("Selection scope", _text(scenario.get("selection_scope"))),
        ("Ticket title", _text(scenario.get("title"))),
        ("Detailed symptom", _compact_detail(scenario.get("customer_symptom"), limit=360)),
        (
            "Detailed trigger / occurrence context",
            _compact_detail(scenario.get("trigger_condition"), limit=360),
        ),
        ("Root-cause evidence", _root_cause_evidence_summary(scenario)),
        ("Solution / recovery evidence", _recovery_evidence_summary(scenario)),
        ("Resolution plan", _resolution_plan_summary(scenario)),
        ("Resolution version", _resolution_version_summary(scenario)),
        (
            "Resolution verification",
            _text(scenario.get("resolution_verification_summary"))
            or "Comparison unavailable.",
        ),
        (
            "Post-target similar ticket evidence",
            _post_target_recurrence_evidence_text(scenario),
        ),
        (
            "Evidence boundary",
            _text(scenario.get("future_resolution_status"))
            or FUTURE_RESOLUTION_STATUS,
        ),
    )
    return [
        {"label": label, "value": value}
        for label, value in values
        if value
    ]


def build_detailed_future_resolution_scenarios(
    records: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Create one detailed scenario per ticket after Issue Group classification."""
    scenarios: list[dict[str, object]] = []
    for index, record in enumerate(
        sorted(records, key=lambda item: _text(item.get("ticket_id"))),
        start=1,
    ):
        ticket_id = _text(record.get("ticket_id"))
        issue_summary = _text(record.get("issue_summary")) or _text(record.get("title"))
        trigger_context = _detailed_trigger_context(record)
        problem_type_id = classify_problem_type(
            {
                "customer_symptom": issue_summary,
                "trigger_condition": trigger_context,
                "observed_behaviour": issue_summary,
                "expected_behaviour": "",
                "rep_title": _text(record.get("title")),
            }
        )
        scenarios.append(
            {
                **dict(record),
                "scenario_id": "SHIFT-TK-{:04d}".format(index),
                "ticket_ids": [ticket_id],
                "ticket_count": 1,
                "problem_type_id": problem_type_id,
                "problem_type": PROBLEM_TYPE_LABELS[problem_type_id],
                "customer_symptom": issue_summary,
                "trigger_condition": trigger_context,
                "recorded_ticket_context": _text(record.get("title")),
                "ticket_lifecycle_status": _ticket_lifecycle_status(record),
                "selection_scope": _text(record.get("selection_scope"))
                or "Open Shift",
            }
        )
    return scenarios


def _detailed_node(
    *,
    tree_id: str,
    node_id: str,
    node_type: str,
    parent_node_id: str,
    data_key: str,
    question_or_action: str,
    visual_label: str,
    scenarios: Sequence[Mapping[str, object]],
    evidence_source: str,
    detail_fields: Sequence[Mapping[str, object]] = (),
    detail_groups: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    ticket_ids = [
        _text(scenario.get("ticket_id"))
        for scenario in scenarios
        if _text(scenario.get("ticket_id"))
    ]
    return {
        "tree_id": tree_id,
        "node_id": node_id,
        "node_type": node_type,
        "parent_node_id": parent_node_id,
        "data_key": data_key,
        "question_or_action": question_or_action,
        "visual_label": visual_label,
        "evidence_source": evidence_source,
        "source_scenario_count": len(scenarios),
        "source_ticket_count": len(ticket_ids),
        "source_scenario_ids": "; ".join(
            _text(scenario.get("scenario_id")) for scenario in scenarios
        ),
        "ticket_ids": ticket_ids,
        "future_resolution_status": (
            FUTURE_RESOLUTION_STATUS
            if node_type == "Future Resolution Plan"
            else ""
        ),
        "solution_status": (
            "ticket_recorded_future_resolution_plan"
            if node_type == "Future Resolution Plan"
            else ""
        ),
        "node_source": "ticket_level_shift_tolerate_future_resolution",
        "detail_fields": [dict(item) for item in detail_fields],
        "detail_groups": [dict(item) for item in detail_groups],
    }


def _cluster_pattern_summary(
    scenarios: Sequence[Mapping[str, object]],
    *,
    field_name: str,
    labels: Mapping[str, str],
    prefix: str,
) -> tuple[str, str]:
    patterns: Counter[str] = Counter()
    for scenario in scenarios:
        value = _text(scenario.get(field_name))
        pattern = (
            extract_symptom_keyword(value)
            if field_name == "customer_symptom"
            else extract_trigger_keyword(value)
        )
        patterns[pattern or "unknown"] += int(scenario.get("ticket_count", 0) or 0)
    ranked = sorted(patterns.items(), key=lambda item: (-item[1], item[0]))
    displayed = ranked[:3]
    summary = "; ".join(
        "{} ({} tickets)".format(labels.get(pattern, pattern), count)
        for pattern, count in displayed
    ) or "No recorded value"
    if len(ranked) > len(displayed):
        summary = "{}; Other observed variants ({} groups)".format(
            summary,
            len(ranked) - len(displayed),
        )
    return "|".join(pattern for pattern, _count in displayed), "{}: {}".format(
        prefix,
        summary,
    )


def _cluster_value_summary(
    values: Sequence[str],
    *,
    empty: str,
    limit: int = 3,
) -> str:
    counts = Counter(value for value in values if value)
    if not counts:
        return empty
    if len(counts) == 1 and next(iter(counts)) == empty:
        return empty
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    summary = [
        "{} ({} ticket(s))".format(_compact_detail(value, limit=420), count)
        for value, count in ranked[:limit]
    ]
    if len(ranked) > limit:
        summary.append("{} additional distinct value(s)".format(len(ranked) - limit))
    return "\n---\n".join(summary)


CURRENT_EVIDENCE_COLUMNS = (
    ("Source Evidence Status", "source_evidence_status"),
    ("Evidence Refreshed At UTC", "evidence_refreshed_at"),
    ("Historical Shift Evidence", "historical_shift_status"),
    ("Current Postponement Status", "current_postponement_status"),
    ("Repair Evidence Status", "repair_status"),
    ("Delivery Evidence Status", "delivery_status"),
    ("LLM Current State Analysis", "current_state_summary"),
    ("LLM Root Cause Evidence Analysis", "root_cause_analysis"),
    ("LLM Recovery Evidence Analysis", "recovery_analysis"),
    ("Current Status Evidence References", "status_evidence"),
    ("Current Assessment Warnings", "assessment_warnings"),
    ("Source Evidence Fingerprint", "evidence_fingerprint"),
    ("LLM Input Fingerprint", "llm_input_fingerprint"),
    ("LLM Input Version", "llm_input_version"),
)


def _current_status_reference_text(record: Mapping[str, object]) -> str:
    evidence = {
        _text(item.get("id")): item
        for item in record.get("current_evidence_references", [])
        if isinstance(item, Mapping)
    }
    references = record.get("status_evidence")
    if not isinstance(references, Mapping):
        return ""
    return "\n".join(
        "{}: {}".format(field, "; ".join(
            "{} [{}]".format(reference, _text(evidence.get(reference, {}).get("date")) or "date not recorded")
            for reference in values
        ) or "No supporting reference")
        for field, values in references.items()
    )


def _current_evidence_values(record: Mapping[str, object]) -> list[str]:
    values = []
    for _header, key in CURRENT_EVIDENCE_COLUMNS:
        value = record.get(key, "")
        if key == "status_evidence":
            value = _current_status_reference_text(record)
        elif isinstance(value, (list, dict)):
            value = json.dumps(value, ensure_ascii=False)
        values.append(_text(value))
    return values


def _cluster_detail_fields(
    scenarios: Sequence[Mapping[str, object]],
) -> list[dict[str, str]]:
    lifecycle_counts = Counter(
        _ticket_lifecycle_status(scenario) for scenario in scenarios
    )
    phase_counts = Counter(_text(scenario.get("phase")) for scenario in scenarios)
    verification_counts = Counter(
        _text(scenario.get("resolution_verification_status"))
        or "comparison_unavailable"
        for scenario in scenarios
    )
    verification_labels = {
        "recurrence_observed": "Recurrence observed",
        "no_recurrence_evidence": "No recurrence evidence (not verified repair)",
        "comparison_unavailable": "Comparison unavailable",
    }
    values = (
        ("Ticket coverage", "{} ticket(s)".format(len(scenarios))),
        (
            "Ticket lifecycle distribution",
            "; ".join(
                "{}: {}".format(status, count)
                for status, count in sorted(lifecycle_counts.items())
                if status
            ),
        ),
        (
            "Ticket phase distribution",
            "; ".join(
                "{}: {}".format(phase, count)
                for phase, count in sorted(phase_counts.items())
                if phase
            ),
        ),
        (
            "Root-cause evidence",
            _cluster_value_summary(
                [_root_cause_evidence_summary(scenario) for scenario in scenarios],
                empty="No recorded root-cause evidence.",
            ),
        ),
        (
            "Solution / recovery evidence",
            _cluster_value_summary(
                [_recovery_evidence_summary(scenario) for scenario in scenarios],
                empty="No recorded solution or recovery evidence.",
            ),
        ),
        (
            "Resolution plan",
            _cluster_value_summary(
                [_resolution_plan_summary(scenario) for scenario in scenarios],
                empty="No structured future-resolution plan recorded.",
            ),
        ),
        (
            "Resolution version",
            _cluster_value_summary(
                [_resolution_version_summary(scenario) for scenario in scenarios],
                empty="No structured resolution version recorded.",
            ),
        ),
        (
            "Resolution verification",
            "; ".join(
                "{}: {}".format(verification_labels.get(status, status), count)
                for status, count in sorted(verification_counts.items())
            ),
        ),
        *(
            (label, "; ".join("{}: {}".format(status, count) for status, count in sorted(Counter(_text(scenario.get(field)) for scenario in scenarios).items()) if status))
            for label, field in (
                ("Source evidence coverage", "source_evidence_status"),
                ("Historical Shift evidence", "historical_shift_status"),
                ("Current postponement assessment", "current_postponement_status"),
                ("Current repair evidence", "repair_status"),
                ("Current delivery evidence", "delivery_status"),
            )
        ),
        (
            "Current progress assessment",
            _cluster_value_summary([_text(scenario.get("current_state_summary")) for scenario in scenarios], empty="")
            if any(_text(scenario.get("current_state_summary")) for scenario in scenarios) else "",
        ),
        ("Evidence boundary", FUTURE_RESOLUTION_STATUS),
    )
    return [
        {"label": label, "value": value}
        for label, value in values
        if value
    ]


def _cluster_raw_ticket_detail_groups(
    scenarios: Sequence[Mapping[str, object]],
) -> list[dict[str, str]]:
    values_by_label: dict[str, list[str]] = defaultdict(list)
    for scenario in scenarios:
        for group in _raw_ticket_detail_groups(scenario):
            label = _text(group.get("label"))
            value = _text(group.get("value"))
            if label and value and value not in values_by_label[label]:
                values_by_label[label].append(value)
        if scenario.get("status_evidence"):
            values_by_label["Current status cited evidence"].append(_text(scenario.get("ticket_id")) + "\n" + _current_status_reference_text(scenario))
        if scenario.get("assessment_warnings"):
            values_by_label["Current assessment review warnings"].append(_text(scenario.get("ticket_id")) + "\n" + "\n".join(scenario["assessment_warnings"]))
    groups = []
    for label, values in sorted(values_by_label.items()):
        visible_values = values[:3]
        text = "\n\n---\n\n".join(visible_values)
        if len(values) > len(visible_values):
            text = "{}\n\n... {} additional distinct evidence record(s) retained in Excel/JSON.".format(
                text,
                len(values) - len(visible_values),
            )
        groups.append({"label": label, "value": text})
    return groups


def build_detailed_future_resolution_trees(
    scenarios: Sequence[Mapping[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Build compact clustered symptom and trigger paths for Shift/Tolerate evidence."""
    scenarios_by_service: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for scenario in scenarios:
        service_function_id = _text(scenario.get("service_function_id")) or "CDUNSPECIFIED"
        scenarios_by_service[service_function_id].append(scenario)

    service_definitions = {
        _text(service.get("service_function_id")): service
        for service in SERVICE_FUNCTION_CATALOG
    }
    all_service_ids = list(service_definitions)
    all_service_ids.extend(
        service_id
        for service_id in sorted(scenarios_by_service)
        if service_id not in service_definitions
    )
    trees: list[dict[str, object]] = []
    nodes: list[dict[str, object]] = []
    for service_function_id in all_service_ids:
        service_scenarios = sorted(
            scenarios_by_service.get(service_function_id, []),
            key=lambda item: _text(item.get("ticket_id")),
        )
        definition = service_definitions.get(service_function_id, {})
        service_function = _text(definition.get("service_function")) or _text(
            service_scenarios[0].get("service_function") if service_scenarios else ""
        ) or "ConnectedDrive / Unspecified"
        tree_id = "DT-{}".format(service_function_id)
        grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
        for scenario in service_scenarios:
            grouped[_text(scenario.get("problem_type_id")) or "other"].append(scenario)
        ordered_groups = sorted(
            grouped.items(),
            key=lambda item: (
                PROBLEM_TYPE_ORDER.get(item[0], 999),
                item[0],
            ),
        )
        tree_nodes = [
            _detailed_node(
                tree_id=tree_id,
                node_id="START",
                node_type="Entry",
                parent_node_id="",
                data_key=service_function_id,
                question_or_action=service_function,
                visual_label=service_function,
                scenarios=service_scenarios,
                evidence_source="V12 Service Function classifier",
                detail_fields=_cluster_detail_fields(service_scenarios)[:3],
            )
        ]
        for group_index, (problem_type_id, group_scenarios) in enumerate(
            ordered_groups,
            start=1,
        ):
            issue_node_id = "ISS-{:02d}".format(group_index)
            issue_label = PROBLEM_TYPE_LABELS.get(problem_type_id, "Other")
            tree_nodes.append(
                _detailed_node(
                    tree_id=tree_id,
                    node_id=issue_node_id,
                    node_type="Issue Group",
                    parent_node_id="START",
                    data_key=problem_type_id,
                    question_or_action=issue_label,
                    visual_label=issue_label,
                    scenarios=group_scenarios,
                    evidence_source="V12 Issue Group classifier",
                )
            )
            symptom_key, symptom_text = _cluster_pattern_summary(
                group_scenarios,
                field_name="customer_symptom",
                labels=SYMPTOM_PATTERN_LABELS,
                prefix="Observed symptom patterns",
            )
            trigger_key, trigger_text = _cluster_pattern_summary(
                group_scenarios,
                field_name="trigger_condition",
                labels=TRIGGER_PATTERN_LABELS,
                prefix="Observed trigger patterns",
            )
            detail_fields = _cluster_detail_fields(group_scenarios)
            detail_groups = _cluster_raw_ticket_detail_groups(group_scenarios)
            symptom_node_id = "SYM-{:02d}".format(group_index)
            trigger_node_id = "TRG-{:02d}".format(group_index)
            plan_node_id = "PLAN-{:02d}".format(group_index)
            tree_nodes.append(
                _detailed_node(
                    tree_id=tree_id,
                    node_id=symptom_node_id,
                    node_type="Symptom Pattern",
                    parent_node_id=issue_node_id,
                    data_key=symptom_key,
                    question_or_action=symptom_text,
                    visual_label=_compact_detail(symptom_text, limit=90),
                    scenarios=group_scenarios,
                    evidence_source="Clustered ticket-specific LLM issue summaries",
                    detail_fields=detail_fields,
                    detail_groups=detail_groups,
                )
            )
            tree_nodes.append(
                _detailed_node(
                    tree_id=tree_id,
                    node_id=trigger_node_id,
                    node_type="Trigger Pattern",
                    parent_node_id=symptom_node_id,
                    data_key=trigger_key,
                    question_or_action=trigger_text,
                    visual_label=_compact_detail(trigger_text, limit=90),
                    scenarios=group_scenarios,
                    evidence_source="Clustered recorded or derived ticket occurrence contexts",
                    detail_fields=detail_fields,
                    detail_groups=detail_groups,
                )
            )
            tree_nodes.append(
                _detailed_node(
                    tree_id=tree_id,
                    node_id=plan_node_id,
                    node_type="Future Resolution Plan",
                    parent_node_id=trigger_node_id,
                    data_key=problem_type_id,
                    question_or_action=_cluster_value_summary(
                        [_resolution_plan_summary(scenario) for scenario in group_scenarios],
                        empty="No structured future-resolution plan recorded.",
                    ),
                    visual_label="{} ticket(s) | Recorded plans".format(
                        len(group_scenarios)
                    ),
                    scenarios=group_scenarios,
                    evidence_source="Clustered ticket-recorded Shift/Tolerate planning evidence",
                    detail_fields=detail_fields,
                    detail_groups=detail_groups,
                )
            )
        ticket_count = len(service_scenarios)
        trees.append(
            {
                "tree_id": tree_id,
                "tree_name": "{} Detailed Shift/Tolerate Tree".format(
                    service_function
                ),
                "service_function_id": service_function_id,
                "service_function": service_function,
                "service_function_label": _text(definition.get("display_name"))
                or service_function,
                "service_domain": _text(definition.get("service_domain"))
                or _text(
                    service_scenarios[0].get("service_domain")
                    if service_scenarios
                    else ""
                ),
                "catalog_status": _text(definition.get("catalog_status"))
                or "ticket_evidence",
                "reference_tree_ids": list(definition.get("reference_tree_ids") or []),
                "scenario_count": ticket_count,
                "ticket_count": ticket_count,
                "node_count": len(tree_nodes),
                "edge_count": max(0, len(tree_nodes) - 1),
                "problem_type_ids": [item[0] for item in ordered_groups],
                "problem_types": [
                    PROBLEM_TYPE_LABELS.get(item[0], "Other")
                    for item in ordered_groups
                ],
                "tree_generation": "clustered_shift_tolerate_future_resolution_v1",
                "guided_branch_count": len(ordered_groups),
                "source_ticket_mention_count": 0,
                "phase09_evidence_status": "mixed_open_and_phase09",
                "diagnostic_layers": [
                    "Issue Group",
                    "Symptom Pattern",
                    "Trigger Pattern",
                    "Future Resolution Plan",
                ],
                "solution_source_policy": FUTURE_RESOLUTION_STATUS,
                "source_status": (
                    "Issue Group, symptom pattern, trigger pattern, and future "
                    "resolution plan are clustered from ticket evidence."
                ),
            }
        )
        nodes.extend(tree_nodes)
    return trees, nodes


def _style_header(sheet, column_count: int, *, row: int = 1) -> None:
    for column_index in range(1, column_count + 1):
        cell = sheet.cell(row=row, column=column_index)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.alignment = Alignment(wrap_text=True, vertical="top")


def _format_sheet(
    sheet,
    headers: Sequence[str],
    widths: Sequence[int],
    *,
    header_row: int = 1,
) -> None:
    _style_header(sheet, len(headers), row=header_row)
    sheet.freeze_panes = "A{}".format(header_row + 1)
    sheet.auto_filter.ref = "A{}:{}{}".format(
        header_row,
        get_column_letter(len(headers)),
        sheet.max_row,
    )
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    for row in sheet.iter_rows(min_row=header_row + 1):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")


def _snapshot_stamp(value: str) -> str:
    digits = "".join(character for character in _text(value) if character.isdigit())
    return digits[:8] or datetime.now(timezone.utc).strftime("%Y%m%d")


def _scenario_plan_text(scenario: Mapping[str, object]) -> str:
    plans = scenario.get("future_resolution_plans")
    if isinstance(plans, list):
        return "\n---\n".join(_text(plan) for plan in plans if _text(plan))
    return _text(scenario.get("future_resolution_plan"))


def _scenario_analysis_text(
    scenario: Mapping[str, object],
    plural_key: str,
    singular_key: str,
) -> str:
    values = scenario.get(plural_key)
    if isinstance(values, list):
        return "\n---\n".join(_text(value) for value in values if _text(value))
    return _text(scenario.get(singular_key))


def _scenario_llm_analysis_text(scenario: Mapping[str, object]) -> str:
    sections = []
    for label, key in (
        ("LLM postponement reason analysis", "postponement_reason_analyses"),
        ("LLM resolution timing analysis", "resolution_timing_analyses"),
        ("LLM resolution version analysis", "resolution_version_analyses"),
    ):
        singular_key = {
            "postponement_reason_analyses": "postponement_reason_analysis",
            "resolution_timing_analyses": "resolution_timing_analysis",
            "resolution_version_analyses": "resolution_version_analysis",
        }[key]
        text = _scenario_analysis_text(scenario, key, singular_key)
        if text:
            sections.append("{}:\n{}".format(label, text))
    return "\n\n".join(sections)


def _v12_future_resolution_scenarios(
    scenarios: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    adapted: list[dict[str, object]] = []
    for scenario in scenarios:
        future_plan = "\n\n".join(
            value
            for value in (
                "{}:\n{}".format(
                    _text(scenario.get("future_resolution_status"))
                    or "Recorded future resolution plan - not verified repair",
                    _scenario_plan_text(scenario)
                ),
                _scenario_llm_analysis_text(scenario),
            )
            if _text(value)
        )
        adapted.append(
            {
                **dict(scenario),
                "problem_type_id": _text(scenario.get("problem_type")) or "other",
                "trigger_condition": _text(scenario.get("trigger_token")) or "other",
                "affected_function": _text(scenario.get("service_function")),
                "affected_function_raw": _text(scenario.get("service_function")),
                "rep_function": _text(scenario.get("service_function")),
                "observed_behaviour": _text(scenario.get("customer_symptom")),
                "expected_behaviour": "",
                "recovery_action": future_plan,
                "closure_decision": "Open Shift/Tolerate future-resolution evidence",
                "evidence_confidence": "ticket_recorded",
                "risk_index": 0,
                "max_risk_score": 0,
                "avg_risk_score": 0,
                "rep_ecu": "",
                "rca_root_cause": "",
                "rca_observed": "",
                "rca_recovery": "",
            }
        )
    return adapted


def _render_v12_future_resolution_catalog_html(
    trees: Sequence[Mapping[str, object]],
    nodes: Sequence[Mapping[str, object]],
) -> str:
    return (
        render_symptom_solution_tree_catalog_html(list(trees), list(nodes))
        .replace(
            "IDCEVO Phase 09 Symptom-to-Solution Tree Catalog",
            "IDCEVO Open Shift/Tolerate Future-Resolution Decision Tree Catalog",
        )
        .replace(
            "Service Function -> Issue Group -> Symptom Pattern -> Trigger Pattern -> Ticket-recorded Solution",
            "Service Function -> Issue Group -> Symptom Pattern -> Trigger Pattern -> Recorded Future Resolution Plan",
        )
        .replace("Ticket-recorded Solution", "Recorded Future Resolution Plan")
        .replace("Ticket Solution", "Future Resolution Plan")
        .replace("ticket_recorded_solution", "ticket_recorded_future_resolution_plan")
        .replace("No recorded ticket solution", "No recorded future resolution plan")
    )


def _build_decision_trees(
    scenarios: Sequence[Mapping[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    scenarios_by_service: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for scenario in scenarios:
        scenarios_by_service[_text(scenario.get("service_function_id"))].append(scenario)

    trees: list[dict[str, object]] = []
    nodes: list[dict[str, object]] = []
    for service_function_id, members in sorted(scenarios_by_service.items()):
        service_function = _text(members[0].get("service_function"))
        tree_id = "SHIFT-DT-{}".format(service_function_id)
        ticket_count = sum(int(item.get("ticket_count", 0) or 0) for item in members)
        root = {
            "tree_id": tree_id,
            "node_id": "START",
            "parent_node_id": "",
            "node_type": "Service Function",
            "data_key": service_function_id,
            "question_or_action": service_function,
            "evidence_source": "V12 Service Function classifier",
            "source_scenario_count": len(members),
            "source_ticket_count": ticket_count,
            "source_scenario_ids": "; ".join(
                _text(item.get("scenario_id")) for item in members
            ),
            "future_resolution_status": "",
        }
        tree_nodes = [root]
        categories: dict[str, list[Mapping[str, object]]] = defaultdict(list)
        for scenario in members:
            category = " / ".join(
                item for item in scenario.get("confidence_categories", []) if _text(item)
            ) or "Target Data Check"
            categories[category].append(scenario)
        for category_index, (category, category_scenarios) in enumerate(
            sorted(categories.items()),
            start=1,
        ):
            category_node_id = "CAT-{:02d}".format(category_index)
            tree_nodes.append(
                {
                    "tree_id": tree_id,
                    "node_id": category_node_id,
                    "parent_node_id": "START",
                    "node_type": "Confidence Category",
                    "data_key": category,
                    "question_or_action": category,
                    "evidence_source": "Open Shift confidence classification",
                    "source_scenario_count": len(category_scenarios),
                    "source_ticket_count": sum(
                        int(item.get("ticket_count", 0) or 0)
                        for item in category_scenarios
                    ),
                    "source_scenario_ids": "; ".join(
                        _text(item.get("scenario_id")) for item in category_scenarios
                    ),
                    "future_resolution_status": "",
                }
            )
            for symptom_index, scenario in enumerate(
                sorted(category_scenarios, key=lambda item: _text(item.get("scenario_id"))),
                start=1,
            ):
                symptom_node_id = "SYM-{:02d}-{:02d}".format(
                    category_index,
                    symptom_index,
                )
                tree_nodes.append(
                    {
                        "tree_id": tree_id,
                        "node_id": symptom_node_id,
                        "parent_node_id": category_node_id,
                        "node_type": "Customer Symptom",
                        "data_key": _text(scenario.get("scenario_id")),
                        "question_or_action": _text(scenario.get("customer_symptom")),
                        "evidence_source": "Fingerprint-validated open-ticket LLM issue summary",
                        "source_scenario_count": 1,
                        "source_ticket_count": int(scenario.get("ticket_count", 0) or 0),
                        "source_scenario_ids": _text(scenario.get("scenario_id")),
                        "future_resolution_status": "",
                    }
                )
                tree_nodes.append(
                    {
                        "tree_id": tree_id,
                        "node_id": "PLAN-{:02d}-{:02d}".format(
                            category_index,
                            symptom_index,
                        ),
                        "parent_node_id": symptom_node_id,
                        "node_type": "Future Resolution Plan",
                        "data_key": _text(scenario.get("scenario_id")),
                        "question_or_action": "\n\n".join(
                            value
                            for value in (
                                "Recorded planning fields:\n{}".format(
                                    _scenario_plan_text(scenario)
                                ),
                                _scenario_llm_analysis_text(scenario),
                            )
                            if _text(value)
                        ),
                        "evidence_source": "Ticket-recorded open Shift planning fields",
                        "source_scenario_count": 1,
                        "source_ticket_count": int(scenario.get("ticket_count", 0) or 0),
                        "source_scenario_ids": _text(scenario.get("scenario_id")),
                        "future_resolution_status": FUTURE_RESOLUTION_STATUS,
                    }
                )
        nodes.extend(tree_nodes)
        trees.append(
            {
                "tree_id": tree_id,
                "tree_name": "{} Open Shift Future-Resolution Tree".format(
                    service_function
                ),
                "service_function_id": service_function_id,
                "service_function": service_function,
                "scenario_count": len(members),
                "ticket_count": ticket_count,
                "node_count": len(tree_nodes),
                "tree_generation": "open_shift_future_resolution_evidence_v1",
                "solution_source_policy": FUTURE_RESOLUTION_STATUS,
                "source_status": "Open-ticket shift and future-resolution evidence",
            }
        )
    return trees, nodes


def _build_ticket_mappings(
    full_records: Sequence[Mapping[str, object]],
    scenarios: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    scenario_by_ticket_id = {
        ticket_id: scenario
        for scenario in scenarios
        for ticket_id in scenario.get("ticket_ids", [])
    }
    mappings = []
    for record in full_records:
        ticket_id = _text(record.get("ticket_id"))
        scenario = scenario_by_ticket_id.get(ticket_id, {})
        mappings.append(
            {
                "ticket_id": ticket_id,
                "scenario_id": _text(scenario.get("scenario_id")),
                "service_function": _text(record.get("service_function")),
                "confidence_category": _text(record.get("confidence_category")),
                "ticket_phase": _text(record.get("phase")),
                "ticket_lifecycle_status": _ticket_lifecycle_status(record),
                "selection_scope": _text(record.get("selection_scope")),
                "rca_root_cause": _text(record.get("rca_root_cause")),
                "rca_recovery": _text(record.get("rca_recovery")),
                "resolution_plan_summary": _resolution_plan_summary(record),
                "resolution_version_summary": _resolution_version_summary(record),
                "resolution_verification_status": _text(
                    record.get("resolution_verification_status")
                )
                or "comparison_unavailable",
                "resolution_verification_summary": _text(
                    record.get("resolution_verification_summary")
                )
                or "Comparison unavailable.",
                "post_target_recurrence_evidence": _post_target_recurrence_evidence_text(
                    record
                ),
                "customer_symptom": _text(record.get("issue_summary")),
                "source_software_version": _text(
                    record.get("source_software_version")
                ),
                "source_release": _text(record.get("source_release")),
                "target_i_step": _text(record.get("target_i_step")),
                "target_release": _text(record.get("target_release")),
                "shift_to_set_pu": _text(record.get("shift_to_set_pu")),
                "first_use_sop_pu": _text(record.get("first_use_sop_pu")),
                "closed_in_version": _text(record.get("closed_in_version")),
                "sw_hrl_closed_version": _text(
                    record.get("sw_hrl_closed_version")
                ),
                "app_version": _text(record.get("app_version")),
                "future_resolution_plan": _text(record.get("future_resolution_plan")),
                        "postponement_reason_analysis": _text(
                            record.get("postponement_reason_analysis")
                        ),
                        "resolution_timing_analysis": _text(
                            record.get("resolution_timing_analysis")
                        ),
                        "resolution_version_analysis": _text(
                            record.get("resolution_version_analysis")
                        ),
                "future_resolution_status": _text(
                    record.get("future_resolution_status")
                ),
                "ticket_title": _text(record.get("title")),
                "creation_time": _text(record.get("creation_time")),
                **{key: record.get(key, "") for _header, key in CURRENT_EVIDENCE_COLUMNS},
                "current_evidence_references": record.get("current_evidence_references", []),
                "llm_current_postponement_status": record.get("llm_current_postponement_status", ""),
            }
        )
    return mappings


def _write_source_workbook(
    full_records: Sequence[Mapping[str, object]],
    output_path: Path,
    *,
    source_snapshot: str,
    raw_evidence: Sequence[Mapping[str, object]],
    artifact_title: str = "IDCEVO Open Shift Tickets FullLLM Evidence Source",
    scope_label: str = "Open Shift / future-resolution candidates only",
) -> None:
    workbook = Workbook()
    overview = workbook.active
    overview.title = "00_总览"
    overview.append([artifact_title])
    overview.append([])
    overview.append(["数据快照", source_snapshot or "Not recorded"])
    overview.append(["范围", scope_label])
    overview.append(["Ticket 数", len(full_records)])
    overview.append(["原始证据行数", len(raw_evidence)])
    overview.append(
        [
            "Description 证据行数",
            sum(_text(item.get("evidence_type")) == "Description" for item in raw_evidence),
        ]
    )
    overview.append(
        [
            "Comment 证据行数",
            sum(_text(item.get("evidence_type")) == "Comment" for item in raw_evidence),
        ]
    )
    overview.append(
        [
            "History 证据行数",
            sum(_text(item.get("evidence_type")) == "History" for item in raw_evidence),
        ]
    )
    overview.append(["治理", FUTURE_RESOLUTION_STATUS])
    overview.column_dimensions["A"].width = 36
    overview.column_dimensions["B"].width = 100
    overview["A1"].font = Font(bold=True, size=14)

    details = workbook.create_sheet("06_全部明细")
    detail_headers = [
        "Ticket ID", "Ticket Creation Time", "Ticket 标题", "Phase", "Service Function",
        "Service Mapping Source", "Confidence Category", "Confidence Basis",
        "Source Software", "Source PU/I-Step", "Target I-Step", "Target PU/I-Step",
        "Shift-to-Set PU", "First Use/SOP PU", "Closed in Version",
        "SW-HRL Closed Version", "App Version", "Future Resolution Status",
        "Future Resolution Plan", "LLM 问题摘要", "LLM 延期原因分析",
        "LLM 解决时间分析", "LLM 解决版本分析", "LLM 延期/计划判断", "LLM 待核实",
        "Function Responsible", "Solution Cluster", "Product Areas",
        "Defect Category", "Assigned ECU", "LLM Source", "LLM Model",
        "Ticket Lifecycle Status", "Selection Scope", "Process Tier", "Process Basis",
        "RCA Root Cause Evidence", "RCA Recovery Action", "Resolution Plan Summary",
        "Resolution Version Summary", "Resolution Verification Status",
        "Resolution Verification Summary", "Post-target Similar Ticket Evidence",
        *[header for header, _key in CURRENT_EVIDENCE_COLUMNS],
    ]
    details.append(detail_headers)
    for record in full_records:
        details.append(
            [
                record.get("ticket_id", ""),
                datetime.fromisoformat(_text(record.get("creation_time")).replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None) if _text(record.get("creation_time")) else None,
                record.get("title", ""), record.get("phase", ""),
                record.get("service_function", ""), record.get("service_mapping_source", ""),
                record.get("confidence_category", ""), record.get("confidence_basis", ""),
                record.get("source_software_version", ""), record.get("source_release", ""),
                record.get("target_i_step", ""), record.get("target_release", ""),
                record.get("shift_to_set_pu", ""), record.get("first_use_sop_pu", ""),
                record.get("closed_in_version", ""), record.get("sw_hrl_closed_version", ""),
                record.get("app_version", ""), record.get("future_resolution_status", ""),
                record.get("future_resolution_plan", ""), record.get("issue_summary", ""),
                record.get("postponement_reason_analysis", ""),
                record.get("resolution_timing_analysis", ""),
                record.get("resolution_version_analysis", ""),
                record.get("postponement_assessment", ""), record.get("review_focus", ""),
                record.get("function_responsible", ""), record.get("solution_cluster", ""),
                record.get("product_areas", ""), record.get("defect_category", ""),
                record.get("assigned_ecu", ""), record.get("llm_source", ""), record.get("llm_model", ""),
                _ticket_lifecycle_status(record), record.get("selection_scope", ""),
                record.get("process_tier", ""), record.get("process_basis", ""),
                record.get("rca_root_cause", ""), record.get("rca_recovery", ""),
                _resolution_plan_summary(record), _resolution_version_summary(record),
                record.get("resolution_verification_status", "comparison_unavailable"),
                record.get("resolution_verification_summary", "Comparison unavailable."),
                _post_target_recurrence_evidence_text(record),
                *_current_evidence_values(record),
            ]
        )
    _format_sheet(
        details,
        detail_headers,
        [14, 24, 62, 22, 28, 34, 34, 70, 52, 18, 30, 18, 18, 18, 44, 44, 36, 42, 80, 60, 72, 72, 72, 72, 60, 60, 32, 32, 42, 32, 24, 16, 24, 26, 42, 34, 70, 80, 80, 80, 80, 30, 90, 80] + [38] * len(CURRENT_EVIDENCE_COLUMNS),
    )
    for row in details.iter_rows(min_row=2, min_col=2, max_col=2):
        row[0].number_format = "yyyy-mm-dd hh:mm:ss"

    evidence = workbook.create_sheet("08_证据原文")
    evidence_headers = [
        "Ticket ID", "Evidence Type", "Timestamp", "Actor", "Field Name",
        "Old Value", "New Value", "Evidence Content", "Chunk Index", "Chunk Count",
    ]
    evidence.append(evidence_headers)
    for raw_record in raw_evidence:
        chunks = _evidence_chunks(raw_record.get("content"))
        for chunk_index, content in enumerate(chunks, start=1):
            evidence.append(
                [
                    _safe_excel_value(_text(raw_record.get("ticket_id"))),
                    _safe_excel_value(_text(raw_record.get("evidence_type"))),
                    _safe_excel_value(_text(raw_record.get("timestamp"))),
                    _safe_excel_value(_text(raw_record.get("actor"))),
                    _safe_excel_value(_text(raw_record.get("field_name"))),
                    _safe_excel_value(_text(raw_record.get("old_value"))),
                    _safe_excel_value(_text(raw_record.get("new_value"))),
                    _safe_excel_value(content),
                    chunk_index,
                    len(chunks),
                ]
            )
    _format_sheet(evidence, evidence_headers, [14, 16, 24, 28, 34, 52, 52, 100, 14, 14])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)


def _write_triple_workbook(
    scenarios: Sequence[Mapping[str, object]],
    full_records: Sequence[Mapping[str, object]],
    trees: Sequence[Mapping[str, object]],
    nodes: Sequence[Mapping[str, object]],
    ticket_mappings: Sequence[Mapping[str, object]],
    output_path: Path,
    *,
    source_snapshot: str,
    artifact_title: str = "IDCEVO Open Shift Symptom-to-Future-Resolution Trees",
    path_label: str = "Service Function -> Confidence Category -> Customer Symptom -> Recorded Future Resolution Plan",
    catalog_filters: Mapping[str, object] | None = None,
) -> None:
    workbook = Workbook()
    overview = workbook.active
    overview.title = "00_总览"
    overview.append([artifact_title])
    overview.append([])
    overview.append(["数据快照", source_snapshot or "Not recorded"])
    overview.append(["问题路径", path_label])
    if catalog_filters:
        overview.append(["HTML ticket filters", json.dumps(catalog_filters, ensure_ascii=False)])
        overview.append(["Filter scope", "HTML view only; this workbook and JSON retain the full ticket scope."])
    overview.append(["Ticket 数", len(full_records)])
    overview.append(["Scenario 数", len(scenarios)])
    overview.append(["Service Function 数", len(trees)])
    overview.append(["治理", FUTURE_RESOLUTION_STATUS])
    overview.column_dimensions["A"].width = 36
    overview.column_dimensions["B"].width = 120
    overview["A1"].font = Font(bold=True, size=14)

    scenario_sheet = workbook.create_sheet("01_Scenario_Library")
    scenario_headers = [
        "Service Function", "Scenario ID", "Ticket ID", "Ticket Creation Time", "Ticket Title",
        "Ticket Phase", "Ticket Lifecycle Status", "Selection Scope",
        "Confidence Category", "Function Responsible", "Customer Symptom",
        "Source Software Version", "Source PU/I-Step", "Target I-Step", "Target PU/I-Step",
        "Shift-to-Set PU", "First Use/SOP PU", "Closed in Version",
        "SW-HRL Closed Version", "App Version", "Future Resolution Status",
        "Recorded Future Resolution Plans", "LLM Postponement Reason Analyses",
        "LLM Resolution Timing Analyses", "LLM Resolution Version Analyses",
        *[header for header, _key in CURRENT_EVIDENCE_COLUMNS],
    ]
    scenario_sheet.append(scenario_headers)
    for scenario in scenarios:
        scenario_sheet.append(
            [
                scenario.get("service_function", ""), scenario.get("scenario_id", ""),
                scenario.get("ticket_id", ""),
                (
                    datetime.fromisoformat(_text(scenario.get("creation_time")).replace("Z", "+00:00"))
                    .astimezone(timezone.utc).replace(tzinfo=None)
                    if _text(scenario.get("creation_time"))
                    else None
                ),
                scenario.get("title", ""),
                scenario.get("phase", ""), scenario.get("ticket_lifecycle_status", ""),
                scenario.get("selection_scope", ""), scenario.get("confidence_category", ""),
                scenario.get("function_responsible", ""), scenario.get("customer_symptom", ""),
                scenario.get("source_software_version", ""), scenario.get("source_release", ""),
                scenario.get("target_i_step", ""), scenario.get("target_release", ""),
                scenario.get("shift_to_set_pu", ""), scenario.get("first_use_sop_pu", ""),
                scenario.get("closed_in_version", ""), scenario.get("sw_hrl_closed_version", ""),
                scenario.get("app_version", ""), scenario.get("future_resolution_status", ""),
                _scenario_plan_text(scenario),
                _scenario_analysis_text(
                    scenario,
                    "postponement_reason_analyses",
                    "postponement_reason_analysis",
                ),
                _scenario_analysis_text(
                    scenario,
                    "resolution_timing_analyses",
                    "resolution_timing_analysis",
                ),
                _scenario_analysis_text(
                    scenario,
                    "resolution_version_analyses",
                    "resolution_version_analysis",
                ),
                *_current_evidence_values(scenario),
            ]
        )
    _format_sheet(scenario_sheet, scenario_headers, [30, 16, 14, 24, 62, 26, 28, 42, 34, 42, 62, 52, 18, 30, 18, 18, 18, 44, 44, 36, 46, 100, 72, 72, 72] + [38] * len(CURRENT_EVIDENCE_COLUMNS))
    creation_column = scenario_headers.index("Ticket Creation Time") + 1
    for row in scenario_sheet.iter_rows(min_row=2, min_col=creation_column, max_col=creation_column):
        row[0].number_format = "yyyy-mm-dd hh:mm:ss"

    found_sheet = workbook.create_sheet("02_Scenario_FoundIn_Function")
    found_headers = [
        "Service Function", "Scenario ID", "Customer Symptom", "Function Responsible",
        "Ticket Count", "Ticket IDs", "Mapping Role",
    ]
    found_sheet.append(found_headers)
    for scenario in scenarios:
        found_sheet.append(
            [
                scenario.get("service_function", ""), scenario.get("scenario_id", ""),
                scenario.get("customer_symptom", ""), "; ".join(scenario.get("function_responsibles", [])),
                scenario.get("ticket_count", 0), "; ".join(scenario.get("ticket_ids", [])),
                "Open shift future-resolution evidence",
            ]
        )
    _format_sheet(found_sheet, found_headers, [30, 16, 62, 42, 12, 72, 38])

    catalog_sheet = workbook.create_sheet("03_Service_Function_Catalog")
    catalog_headers = [
        "Service Function", "Scenario Count", "Ticket Count", "Node Count",
        "Tree Generation", "Future Resolution Policy", "Source Status",
    ]
    catalog_sheet.append(catalog_headers)
    for tree in trees:
        catalog_sheet.append(
            [
                tree.get("service_function", ""), tree.get("scenario_count", 0),
                tree.get("ticket_count", 0), tree.get("node_count", 0),
                tree.get("tree_generation", ""), tree.get("solution_source_policy", ""),
                tree.get("source_status", ""),
            ]
        )
    _format_sheet(catalog_sheet, catalog_headers, [32, 14, 14, 14, 42, 62, 56])

    tree_sheet = workbook.create_sheet("04_Decision_Tree_Catalog")
    tree_headers = [
        "Service Function", "Tree ID", "Scenario Count", "Ticket Count", "Node Count",
        "Tree Generation", "Future Resolution Policy", "Visual Catalog",
    ]
    tree_sheet.append(tree_headers)
    for tree in trees:
        tree_sheet.append(
            [
                tree.get("service_function", ""), tree.get("tree_id", ""),
                tree.get("scenario_count", 0), tree.get("ticket_count", 0),
                tree.get("node_count", 0), tree.get("tree_generation", ""),
                tree.get("solution_source_policy", ""), "See HTML catalog",
            ]
        )
    _format_sheet(tree_sheet, tree_headers, [32, 24, 14, 14, 14, 42, 62, 32])

    node_sheet = workbook.create_sheet("05_Decision_Tree_Nodes")
    node_headers = [
        "Service Function", "Tree ID", "Node ID", "Parent Node ID", "Node Type",
        "Question / Future Resolution Plan", "Evidence Source", "Source Scenario Count",
        "Source Ticket Count", "Source Scenario IDs", "Future Resolution Status",
        "Visual Label", "Selected Node Detail",
    ]
    node_sheet.append(node_headers)
    tree_service = {tree["tree_id"]: tree["service_function"] for tree in trees}
    for node in nodes:
        node_sheet.append(
            [
                tree_service.get(_text(node.get("tree_id")), ""), node.get("tree_id", ""),
                node.get("node_id", ""), node.get("parent_node_id", ""), node.get("node_type", ""),
                node.get("question_or_action", ""), node.get("evidence_source", ""),
                node.get("source_scenario_count", 0), node.get("source_ticket_count", 0),
                node.get("source_scenario_ids", ""), node.get("future_resolution_status", ""),
                node.get("visual_label", ""), "\n\n".join(
                    "{}: {}".format(
                        _text(item.get("label")),
                        _text(item.get("value")),
                    )
                    for item in node.get("detail_fields", [])
                    if isinstance(item, Mapping)
                ),
            ]
        )
    _format_sheet(node_sheet, node_headers, [30, 28, 20, 20, 28, 100, 46, 18, 18, 72, 46, 64, 100])

    cards_sheet = workbook.create_sheet("06_Service_Future_Plan_Cards")
    card_headers = [
        "Service Function", "Scenario ID", "Customer Symptom", "Ticket Count",
        "Recorded Future Resolution Plans", "LLM Postponement Reason Analyses",
        "LLM Resolution Timing Analyses", "LLM Resolution Version Analyses",
        "Future Resolution Status", "Review Boundary",
    ]
    cards_sheet.append(card_headers)
    for scenario in scenarios:
        cards_sheet.append(
            [
                scenario.get("service_function", ""), scenario.get("scenario_id", ""),
                scenario.get("customer_symptom", ""), scenario.get("ticket_count", 0),
                _scenario_plan_text(scenario),
                _scenario_analysis_text(
                    scenario,
                    "postponement_reason_analyses",
                    "postponement_reason_analysis",
                ),
                _scenario_analysis_text(
                    scenario,
                    "resolution_timing_analyses",
                    "resolution_timing_analysis",
                ),
                _scenario_analysis_text(
                    scenario,
                    "resolution_version_analyses",
                    "resolution_version_analysis",
                ),
                scenario.get("future_resolution_status", ""),
                "Ticket-recorded plan only; verify delivery, integration, and complaint applicability.",
            ]
        )
    _format_sheet(cards_sheet, card_headers, [30, 16, 62, 12, 100, 72, 72, 72, 46, 72])

    mapping_sheet = workbook.create_sheet("07_Ticket_Mapping")
    mapping_headers = [
        "Ticket ID", "Scenario ID", "Service Function", "Confidence Category",
        "Customer Symptom", "LLM Postponement Reason Analysis", "LLM Resolution Timing Analysis",
        "LLM Resolution Version Analysis", "Future Resolution Status", "Future Resolution Plan", "Ticket Title",
    ]
    mapping_sheet.append(mapping_headers)
    for mapping in ticket_mappings:
        mapping_sheet.append(
            [
                mapping.get("ticket_id", ""), mapping.get("scenario_id", ""),
                mapping.get("service_function", ""), mapping.get("confidence_category", ""),
                mapping.get("customer_symptom", ""), mapping.get("postponement_reason_analysis", ""),
                mapping.get("resolution_timing_analysis", ""), mapping.get("resolution_version_analysis", ""),
                mapping.get("future_resolution_status", ""), mapping.get("future_resolution_plan", ""), mapping.get("ticket_title", ""),
            ]
        )
    _format_sheet(mapping_sheet, mapping_headers, [14, 16, 30, 34, 62, 72, 72, 72, 46, 100, 62])

    quality_sheet = workbook.create_sheet("08_Data_Quality")
    quality_headers = ["Metric", "Count", "Meaning"]
    quality_sheet.append(quality_headers)
    quality_rows = [
        ("Open shift tickets", len(full_records), "Tickets in the source workbook"),
        ("Scenario count", len(scenarios), "Service Function plus normalized LLM issue summary"),
        ("Mapped service functions", len(trees), "V12 classifier result"),
        ("Validated LLM summaries", len(full_records), "Fingerprint-validated v2 summary evidence"),
        ("Verified repairs", 0, "Intentionally zero: this artifact records future plans, not verified repairs"),
    ]
    for row in quality_rows:
        quality_sheet.append(row)
    _format_sheet(quality_sheet, quality_headers, [34, 16, 100])

    dictionary_sheet = workbook.create_sheet("09_Data_Dictionary")
    dictionary_headers = ["Field", "Definition", "Governance Rule"]
    dictionary_sheet.append(dictionary_headers)
    for row in (
        ("Service Function", "V12 Service Function classifier result", "Customer-facing navigation entry"),
        ("Ticket Creation Time", "Octane ticket creation timestamp in UTC", "Excel date value; missing source timestamps remain blank"),
        ("Customer Symptom", "Fingerprint-validated LLM issue summary", "Ticket evidence summary; not a diagnosis"),
        ("Future Resolution Plan", "Target I-Step, Shift-to-Set, Phase 07 closed version, or related recorded planning field", FUTURE_RESOLUTION_STATUS),
        ("Confidence Category", "Exclusive highest-confidence evidence classification", "One category per ticket"),
        ("Future Resolution Status", FUTURE_RESOLUTION_STATUS, "Never interpreted as delivery completion or approved repair"),
    ):
        dictionary_sheet.append(row)
    _format_sheet(dictionary_sheet, dictionary_headers, [34, 70, 100])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)


def _catalog_html(payload: Mapping[str, object]) -> str:
    safe_payload = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>IDCEVO Open Shift Future-Resolution Catalog</title>
<style>
:root { --ink:#17212b; --muted:#5f6f7e; --line:#c9d4dc; --panel:#ffffff; --blue:#164a72; --teal:#147c78; --amber:#ab6a10; --bg:#edf3f5; }
* { box-sizing:border-box; }
body { margin:0; color:var(--ink); background:linear-gradient(135deg,#edf3f5 0,#f8fbfc 55%,#e8f0ec 100%); font:14px/1.45 "IBM Plex Sans", "Segoe UI", sans-serif; }
header { padding:28px max(24px,calc((100vw - 1440px)/2)); border-bottom:4px solid var(--teal); background:#f8fbfc; }
h1 { margin:0; font-size:26px; letter-spacing:0; } .subtitle { margin:6px 0 0; color:var(--muted); max-width:1040px; }
main { max-width:1440px; margin:0 auto; padding:22px 24px 48px; }
.metrics { display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:10px; margin-bottom:18px; }.metric { background:var(--panel); border:1px solid var(--line); border-radius:6px; padding:12px; }.metric b { display:block; font-size:22px; color:var(--blue); }.metric span { color:var(--muted); }
.controls { display:flex; flex-wrap:wrap; gap:12px; align-items:end; padding:14px; background:var(--panel); border:1px solid var(--line); border-radius:6px; }.control { display:grid; gap:4px; min-width:240px; }.control label { color:var(--muted); font-size:12px; }.control select { padding:8px; border:1px solid #8fa2ae; border-radius:4px; background:white; color:var(--ink); }
.notice { margin:18px 0; padding:12px 14px; border-left:4px solid var(--amber); background:#fff7e8; color:#613d07; }.tree { display:grid; gap:14px; }.service { background:var(--panel); border:1px solid var(--line); border-radius:6px; overflow:hidden; }.service h2 { margin:0; padding:14px 16px; color:white; background:var(--blue); font-size:17px; }.category { padding:14px 16px; border-top:1px solid var(--line); }.category h3 { margin:0 0 10px; color:var(--teal); font-size:15px; }.scenario { margin:10px 0 0 16px; padding:12px; border-left:3px solid #92bcc0; background:#f7fafb; }.scenario h4 { margin:0 0 8px; font-size:14px; }.plan { margin-top:8px; padding:10px; border:1px solid #d4e1e5; background:white; white-space:pre-wrap; }.meta { color:var(--muted); font-size:12px; }.tickets { margin-top:8px; color:var(--muted); font-size:12px; word-break:break-word; }
@media (max-width:640px) { header { padding:20px; } main { padding:14px; } h1 { font-size:22px; } .control { min-width:100%; } .scenario { margin-left:0; } }
</style>
</head>
<body>
<header><h1>IDCEVO Open Shift Future-Resolution Catalog</h1><p class="subtitle">Service Function -> Confidence Category -> Customer Symptom -> Recorded Future Resolution Plan. This catalog tracks open tickets and recorded follow-up plans; it does not claim a verified repair.</p></header>
<main><section class="metrics" id="metrics"></section><section class="controls"><div class="control"><label for="service">Service Function</label><select id="service"></select></div><div class="control"><label for="category">Confidence Category</label><select id="category"></select></div></section><p class="notice">Recorded future resolution plan - not verified repair. Verify integration, delivery, and current complaint applicability before communicating a resolution.</p><section class="tree" id="tree"></section></main>
<script>const CATALOG = """ + safe_payload + """;
const text=value=>String(value??""); const byId=id=>document.getElementById(id); const esc=value=>text(value);
function options(id, values, label){ const select=byId(id); select.replaceChildren(); const all=document.createElement('option'); all.value=''; all.textContent=label; select.append(all); values.forEach(value=>{const option=document.createElement('option'); option.value=value; option.textContent=value; select.append(option);}); }
function renderMetrics(){ const summary=CATALOG.summary||{}; const items=[['Tickets',summary.ticket_count],['Scenarios',summary.scenario_count],['Service functions',summary.service_function_count],['Verified repairs',0]]; const root=byId('metrics'); root.replaceChildren(); items.forEach(([label,value])=>{const card=document.createElement('div'); card.className='metric'; const num=document.createElement('b'); num.textContent=text(value); const caption=document.createElement('span'); caption.textContent=label; card.append(num,caption); root.append(card);}); }
function groupedScenarios(){ const service=byId('service').value; const category=byId('category').value; return (CATALOG.scenarios||[]).filter(item=>(!service||item.service_function===service)&&(!category||(item.confidence_categories||[]).includes(category))); }
function render(){ const grouped={}; groupedScenarios().forEach(item=>{const service=item.service_function||'Unclassified'; const category=(item.confidence_categories||['Target Data Check']).join(' / '); ((grouped[service]??={})[category]??=[]).push(item);}); const root=byId('tree'); root.replaceChildren(); Object.keys(grouped).sort().forEach(service=>{const box=document.createElement('section'); box.className='service'; const title=document.createElement('h2'); title.textContent=service; box.append(title); Object.keys(grouped[service]).sort().forEach(category=>{const section=document.createElement('section'); section.className='category'; const heading=document.createElement('h3'); heading.textContent=category; section.append(heading); grouped[service][category].forEach(item=>{const card=document.createElement('article'); card.className='scenario'; const symptom=document.createElement('h4'); symptom.textContent=item.customer_symptom; const meta=document.createElement('div'); meta.className='meta'; meta.textContent=`${item.scenario_id} | ${item.ticket_count} ticket(s) | ${item.future_resolution_status}`; const rawPlans=(item.future_resolution_plans||[]).join('\\n---\\n'); const analyses=[['LLM postponement reason analysis',item.postponement_reason_analyses],['LLM resolution timing analysis',item.resolution_timing_analyses],['LLM resolution version analysis',item.resolution_version_analyses]].map(([label,values])=>Array.isArray(values)&&values.length?`${label}:\\n${values.join('\\n---\\n')}`:'').filter(Boolean).join('\\n\\n'); const plan=document.createElement('div'); plan.className='plan'; plan.textContent=[rawPlans,analyses].filter(Boolean).join('\\n\\n')||'No recorded future resolution plan'; const tickets=document.createElement('div'); tickets.className='tickets'; tickets.textContent=`Tickets: ${(item.ticket_ids||[]).join(', ')}`; card.append(symptom,meta,plan,tickets); section.append(card);}); box.append(section);}); root.append(box);}); if(!root.children.length){const empty=document.createElement('p'); empty.textContent='No scenarios match the current filters.'; root.append(empty);} }
const services=[...new Set((CATALOG.scenarios||[]).map(item=>item.service_function).filter(Boolean))].sort(); const categories=[...new Set((CATALOG.scenarios||[]).flatMap(item=>item.confidence_categories||[]).filter(Boolean))].sort(); options('service',services,'All Service Functions'); options('category',categories,'All Confidence Categories'); byId('service').addEventListener('change',render); byId('category').addEventListener('change',render); renderMetrics(); render();
</script></body></html>"""


def build_open_shift_artifact_triple(
    full_records: Sequence[Mapping[str, object]],
    *,
    output_directory: str | Path,
    source_snapshot: str = "",
    raw_evidence: Sequence[Mapping[str, object]] = (),
) -> dict[str, Path]:
    resolved_directory = Path(output_directory)
    stamp = _snapshot_stamp(source_snapshot)
    source_xlsx = resolved_directory / "IDCEVO_Open_Shift_Tickets_{}_FullLLM.xlsx".format(stamp)
    stem = "IDCEVO_Open_Shift_Scenario_Library_V12_Symptom_to_Future_Resolution_Trees"
    triple_xlsx = resolved_directory / "{}.xlsx".format(stem)
    knowledge_base_json = resolved_directory / "{}_Decision_Tree_Knowledge_Base.json".format(stem)
    catalog_html = resolved_directory / "{}_Decision_Tree_Catalog.html".format(stem)
    scenarios = build_shift_scenarios(full_records)
    v12_scenarios = _v12_future_resolution_scenarios(scenarios)
    trees, nodes, _ = build_symptom_solution_trees(v12_scenarios, [])
    ticket_mappings = _build_ticket_mappings(full_records, scenarios)
    payload: dict[str, object] = {
        "schema_version": "open-shift-scenario-kb-v1",
        "artifact_type": "open_shift_future_resolution_evidence",
        "source_snapshot": source_snapshot,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "governance": {
            "future_resolution_status": FUTURE_RESOLUTION_STATUS,
            "statement": "Future resolution fields are ticket-recorded plans, not verified repairs or approved service instructions.",
        },
        "summary": {
            "ticket_count": len(full_records),
            "scenario_count": len(scenarios),
            "service_function_count": len(trees),
            "verified_repair_count": 0,
        },
        "service_functions": trees,
        "decision_trees": trees,
        "decision_tree_nodes": nodes,
        "scenarios": scenarios,
        "ticket_mappings": ticket_mappings,
    }
    _write_source_workbook(
        full_records,
        source_xlsx,
        source_snapshot=source_snapshot,
        raw_evidence=raw_evidence,
    )
    _write_triple_workbook(
        scenarios,
        full_records,
        trees,
        nodes,
        ticket_mappings,
        triple_xlsx,
        source_snapshot=source_snapshot,
    )
    knowledge_base_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    catalog_html.write_text(
        _render_v12_future_resolution_catalog_html(trees, nodes),
        encoding="utf-8",
    )
    return {
        "source_xlsx": source_xlsx,
        "triple_xlsx": triple_xlsx,
        "knowledge_base_json": knowledge_base_json,
        "catalog_html": catalog_html,
    }


TICKET_FILTER_DEFAULTS = {
    "excluded_phases": ["05", "08"],
    "created_from": "",
    "created_through": "",
    "date_timezone": "UTC",
}


def _detailed_future_resolution_catalog_payload(
    trees: Sequence[Mapping[str, object]],
    nodes: Sequence[Mapping[str, object]],
    scenarios: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    nodes_by_tree: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for node in nodes:
        nodes_by_tree[_text(node.get("tree_id"))].append(node)
    scenarios_by_service: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for scenario in scenarios:
        scenarios_by_service[_text(scenario.get("service_function"))].append(scenario)

    functions: list[dict[str, object]] = []
    for tree in trees:
        tree_id = _text(tree.get("tree_id"))
        service_function = _text(tree.get("service_function"))
        functions.append(
            {
                **dict(tree),
                "data_tree_nodes": [
                    {
                        "node_id": _text(node.get("node_id")),
                        "parent_node_id": _text(node.get("parent_node_id")),
                        "node_type": _text(node.get("node_type")),
                        "data_key": _text(node.get("data_key")),
                        "question_or_solution": _text(
                            node.get("question_or_action")
                        ),
                        "visual_label": _text(node.get("visual_label")),
                        "evidence_source": _text(node.get("evidence_source")),
                        "source_scenario_count": int(
                            node.get("source_scenario_count", 0) or 0
                        ),
                        "source_ticket_count": int(
                            node.get("source_ticket_count", 0) or 0
                        ),
                        "source_scenario_ids": _text(
                            node.get("source_scenario_ids")
                        ),
                        "ticket_ids": list(node.get("ticket_ids") or []),
                        "solution_status": _text(node.get("solution_status")),
                        "node_source": _text(node.get("node_source")),
                        "detail_fields": [
                            dict(item)
                            for item in node.get("detail_fields", [])
                            if isinstance(item, Mapping)
                        ],
                        "detail_groups": [
                            dict(item)
                            for item in node.get("detail_groups", [])
                            if isinstance(item, Mapping)
                        ],
                    }
                    for node in nodes_by_tree.get(tree_id, [])
                ],
                "scenario_records": [
                    {
                        "scenario_id": _text(scenario.get("scenario_id")),
                        "problem_type": _text(scenario.get("problem_type")),
                        "customer_symptom": _text(
                            scenario.get("customer_symptom")
                        ),
                        "trigger_condition": _text(
                            scenario.get("trigger_condition")
                        ),
                        "ticket_count": int(scenario.get("ticket_count", 0) or 0),
                        "risk_band": _text(
                            scenario.get("ticket_lifecycle_status")
                        ),
                        "risk_index": _text(scenario.get("selection_scope")),
                        "representative_ticket_id": _text(
                            scenario.get("ticket_id")
                        ),
                        "ticket_phase": _text(scenario.get("phase")),
                        "creation_time": _text(scenario.get("creation_time")),
                        "ticket_lifecycle_status": _ticket_lifecycle_status(scenario),
                        **{
                            field: _text(scenario.get(field))
                            for field in (
                                "source_evidence_status", "historical_shift_status",
                                "current_postponement_status", "repair_status", "delivery_status",
                                "resolution_verification_status",
                            )
                        },
                        "filter_detail_values": {
                            "Root-cause evidence": _root_cause_evidence_summary(scenario),
                            "Solution / recovery evidence": _recovery_evidence_summary(scenario),
                            "Resolution plan": _resolution_plan_summary(scenario),
                            "Resolution version": _resolution_version_summary(scenario),
                            "Current progress assessment": _text(scenario.get("current_state_summary")),
                            "Evidence boundary": FUTURE_RESOLUTION_STATUS,
                        },
                        "filter_detail_groups": _cluster_raw_ticket_detail_groups([scenario]),
                        "representative_title": _text(scenario.get("title")),
                        "recorded_recovery_action": _text(
                            scenario.get("future_resolution_plan")
                        ),
                        "evidence_confidence": _text(
                            scenario.get("future_resolution_status")
                        ),
                    }
                    for scenario in scenarios_by_service.get(service_function, [])
                ],
                "source_graphs": [],
            }
        )
    return {
        "schema_version": "shift-tolerate-future-resolution-catalog-v1",
        "ticket_filters": dict(TICKET_FILTER_DEFAULTS),
        "summary": {
            "service_function_count": len(functions),
            "field_scenario_count": len(scenarios),
            "field_ticket_count": len(scenarios),
            "image_backed_function_count": 0,
            "ai_structured_source_graph_count": 0,
            "ai_structured_source_node_count": 0,
        },
        "functions": functions,
    }


def _render_detailed_future_resolution_catalog_html(
    trees: Sequence[Mapping[str, object]],
    nodes: Sequence[Mapping[str, object]],
    scenarios: Sequence[Mapping[str, object]],
) -> str:
    catalog = _detailed_future_resolution_catalog_payload(trees, nodes, scenarios)
    return (
        render_all_function_catalog_html(catalog)
        .replace(
            "All-Function After-Sales Decision Tree Catalog",
            "IDCEVO Unified Shift/Tolerate Future-Resolution Decision Tree Catalog",
        )
        .replace(
            "All V12 service functions, their evidence-derived decision trees, and AI-structured original after-sales reference trees where source images are available.",
            "Offline database snapshot analysis; online freshness and fetch completeness are not verified. Clustered nodes retain ticket-specific evidence, repair progress and recorded delivery plans."
            if any(scenario.get("source_evidence_status") == "database_snapshot" for scenario in scenarios)
            else "Merged Open Shift and strict Shift/Tolerate-process evidence. Issue Groups remain aggregated; lower nodes retain ticket-specific symptom, occurrence context, lifecycle, and future-resolution details.",
        )
        .replace("Field scenarios", "Clustered evidence scenarios")
        .replace("Field tickets", "Shift/Tolerate tickets")
        .replace("V12 historical scenarios", "Clustered Shift/Tolerate evidence")
        .replace(
            '"Ticket Solution": "#e2ece8" };',
            '"Ticket Solution": "#e2ece8", "Future Resolution Plan": "#e2ece8" };',
        )
    )


def build_merged_shift_tolerate_artifact_triple(
    full_records: Sequence[Mapping[str, object]],
    *,
    output_directory: str | Path,
    source_snapshot: str = "",
    raw_evidence: Sequence[Mapping[str, object]] = (),
    source_evidence_audit: Mapping[str, object] | None = None,
) -> dict[str, Path]:
    """Write one candidate V12 triple for the merged Shift/Tolerate cohort."""
    current_ids = {_text(record.get("ticket_id")) for record in full_records if _text(record.get("source_evidence_status")) == "current_verified"}
    snapshot_ids = {_text(record.get("ticket_id")) for record in full_records if _text(record.get("source_evidence_status")) == "database_snapshot"}
    unavailable_ids = {_text(record.get("ticket_id")) for record in full_records if _text(record.get("source_evidence_status")) == "unavailable_preserved"}
    if (current_ids or snapshot_ids) and not source_evidence_audit:
        raise ValueError("Current records require a verified source evidence audit")
    if source_evidence_audit and (
        (current_ids and source_evidence_audit.get("canonical_source_verified") is not True)
        or (snapshot_ids and source_evidence_audit.get("database_snapshot_integrity_verified") is not True)
        or source_evidence_audit.get("current_verified_ticket_count") != len(current_ids)
        or source_evidence_audit.get("database_snapshot_ticket_count", 0) != len(snapshot_ids)
        or source_evidence_audit.get("requested_ticket_count") != len(full_records)
        or set(source_evidence_audit.get("unavailable_ticket_ids", [])) != unavailable_ids
        or len(current_ids | snapshot_ids | unavailable_ids) != len(full_records)
    ):
        raise ValueError("Current record coverage does not match the verified source evidence audit")
    resolved_directory = Path(output_directory)
    stamp = _snapshot_stamp(source_snapshot)
    source_xlsx = resolved_directory / (
        "IDCEVO_Combined_Open_Phase09_Shift_Tolerate_Tickets_{}_FullLLM.xlsx".format(
            stamp
        )
    )
    stem = (
        "IDCEVO_Combined_Open_Phase09_Shift_Tolerate_Scenario_Library_V12_"
        "Symptom_to_Future_Resolution_Trees"
    )
    triple_xlsx = resolved_directory / "{}.xlsx".format(stem)
    knowledge_base_json = resolved_directory / "{}_Decision_Tree_Knowledge_Base.json".format(
        stem
    )
    catalog_html = resolved_directory / "{}_Decision_Tree_Catalog.html".format(stem)
    scenarios = build_detailed_future_resolution_scenarios(full_records)
    trees, nodes = build_detailed_future_resolution_trees(scenarios)
    ticket_mappings = _build_ticket_mappings(full_records, scenarios)
    open_count = sum(
        _ticket_lifecycle_status(record) == "Open / active"
        for record in full_records
    )
    payload: dict[str, object] = {
        "schema_version": "merged-shift-tolerate-scenario-kb-v1",
        "artifact_type": "merged_shift_tolerate_future_resolution_evidence",
        "source_snapshot": source_snapshot,
        "source_evidence_audit": dict(source_evidence_audit or {}),
        "catalog_filters": dict(TICKET_FILTER_DEFAULTS),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "governance": {
            "future_resolution_status": FUTURE_RESOLUTION_STATUS,
            "statement": (
                "Future resolution fields are ticket-recorded plans, not verified "
                "repairs or approved service instructions."
            ),
        },
        "summary": {
            "ticket_count": len(full_records),
            "open_ticket_count": open_count,
            "closed_phase09_ticket_count": sum(_ticket_lifecycle_status(record) == "Closed / Phase 09" for record in full_records),
            "closed_phase06_ticket_count": sum(_ticket_lifecycle_status(record) == "Closed / Phase 06" for record in full_records),
            "historical_unavailable_ticket_count": sum(_text(record.get("source_evidence_status")) == "unavailable_preserved" for record in full_records),
            "current_evidence_verified_count": sum(_text(record.get("source_evidence_status")) == "current_verified" for record in full_records),
            "database_snapshot_ticket_count": len(snapshot_ids),
            "current_assessment_counts": {field: dict(Counter(_text(record.get(field)) for record in full_records)) for field in ("historical_shift_status", "current_postponement_status", "repair_status", "delivery_status")},
            "current_assessment_warning_count": sum(bool(record.get("assessment_warnings")) for record in full_records),
            "scenario_count": len(scenarios),
            "service_function_count": len(trees),
            "verified_repair_count": 0,
        },
        "service_functions": trees,
        "decision_trees": trees,
        "decision_tree_nodes": nodes,
        "scenarios": scenarios,
        "ticket_mappings": ticket_mappings,
    }
    _write_source_workbook(
        full_records,
        source_xlsx,
        source_snapshot=source_snapshot,
        raw_evidence=raw_evidence,
        artifact_title="IDCEVO Shift/Tolerate Database Snapshot FullLLM Evidence Source" if snapshot_ids else "IDCEVO Combined Open + Phase 09 Shift/Tolerate FullLLM Evidence Source",
        scope_label="Offline database snapshot; online freshness and fetch completeness not verified" if snapshot_ids else "Merged Open Shift and strict Shift/Tolerate process evidence",
    )
    _write_triple_workbook(
        scenarios,
        full_records,
        trees,
        nodes,
        ticket_mappings,
        triple_xlsx,
        source_snapshot=source_snapshot,
        artifact_title="IDCEVO Shift/Tolerate Database Snapshot Decision Trees - Not Live-Verified" if snapshot_ids else "IDCEVO Combined Open + Phase 09 Shift/Tolerate Decision Trees",
        path_label=(
            "Service Function -> Issue Group -> Clustered Symptom Pattern -> "
            "Clustered Trigger Pattern -> Future Resolution Plan"
        ),
        catalog_filters=TICKET_FILTER_DEFAULTS,
    )
    knowledge_base_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    catalog_html.write_text(
        _render_detailed_future_resolution_catalog_html(trees, nodes, scenarios),
        encoding="utf-8",
    )
    return {
        "source_xlsx": source_xlsx,
        "triple_xlsx": triple_xlsx,
        "knowledge_base_json": knowledge_base_json,
        "catalog_html": catalog_html,
    }