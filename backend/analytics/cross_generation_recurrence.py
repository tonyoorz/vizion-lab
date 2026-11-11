from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from copy import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


HOSTS = ("MGU", "IDC", "IDCEVO")
HOST_TRANSITIONS = (
    ("MGU", "IDC"),
    ("IDC", "IDCEVO"),
    ("MGU", "IDCEVO"),
)
BROAD_FUNCTION_AREAS = frozenset({"bs2", "stability", "performance"})
SCHEMA_VERSION = "cross-generation-recurrence-v1"
REVIEW_STATUS = "human_review_required"


def _text(value: object) -> str:
    return str(value or "").strip()


def _parse_creation_time(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = _text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _excel_utc(value: object) -> datetime | None:
    parsed = _parse_creation_time(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _resolve_host(record: Mapping[str, object]) -> str:
    canonical = {host.casefold(): host for host in HOSTS}
    for field_name in ("tproject", "project"):
        value = _text(record.get(field_name)).casefold()
        if value in canonical:
            return canonical[value]
    return ""


def _function_area(record: Mapping[str, object]) -> str:
    return re.sub(r"\s+", " ", _text(record.get("product_areas")))


def _is_broad_function_area(value: object) -> bool:
    return _function_area({"product_areas": value}).casefold() in BROAD_FUNCTION_AREAS


def _normalize_title(value: object) -> str:
    text = unicodedata.normalize("NFKC", _text(value)).casefold()
    text = re.sub(
        r"(?<![a-z0-9])(?:mgu(?:18|21|22)?|idc(?:23)?|idcevo|(?:hu[-_ ]?)?mgu[-_ ]?02[-_ ]?[al])(?![a-z0-9])",
        " ",
        text,
    )
    text = re.sub(r"\b20\d{2}[-_/]?\d{2}[-_/]?\d{2}\b", " ", text)
    text = re.sub(r"\b\d{2}w\d{2}(?:\.\d+)?\b", " ", text)
    text = re.sub(r"\b\d+(?:\.\d+){1,4}\b", " # ", text)
    text = re.sub(r"\d+", " # ", text)
    text = re.sub(r"[_|/\\-]+", " ", text)
    text = re.sub(r"[^\w\s#]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _prepared_records(records: Sequence[Mapping[str, object]]) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    seen_ticket_ids: set[str] = set()
    for record in records:
        ticket_id = _text(record.get("defect_id") or record.get("ticket_id"))
        host = _resolve_host(record)
        creation_time = _parse_creation_time(record.get("creation_time"))
        title = _text(record.get("name") or record.get("title"))
        if not ticket_id or ticket_id in seen_ticket_ids or not host or creation_time is None or not title:
            continue
        seen_ticket_ids.add(ticket_id)
        prepared.append(
            {
                "record": record,
                "ticket_id": ticket_id,
                "host": host,
                "creation_time": creation_time,
                "title": title,
                "normalized_title": _normalize_title(title),
                "service_function": _function_area(record),
                "relation_reference": _text(record.get("relation_to")),
            }
        )
    return prepared


def _candidate_key(source: Mapping[str, Any], target: Mapping[str, Any]) -> tuple[str, str]:
    return str(source["ticket_id"]), str(target["ticket_id"])


def _candidate_service_function(source: Mapping[str, Any], target: Mapping[str, Any]) -> str:
    source_function = _text(source.get("service_function"))
    target_function = _text(target.get("service_function"))
    if source_function and source_function == target_function:
        return source_function
    if source_function:
        return source_function
    if target_function:
        return target_function
    return "Unmapped shared relation reference"


def _new_candidate(
    source: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    kind: str,
    evidence: str,
    relation_reference: str = "",
) -> dict[str, Any]:
    lag_days = (target["creation_time"] - source["creation_time"]).days
    exact_title = source["normalized_title"] == target["normalized_title"]
    return {
        "source_ticket_id": source["ticket_id"],
        "target_ticket_id": target["ticket_id"],
        "source_host": source["host"],
        "target_host": target["host"],
        "source_creation_time": source["creation_time"].isoformat(),
        "target_creation_time": target["creation_time"].isoformat(),
        "source_title": source["title"],
        "target_title": target["title"],
        "source_assigned_ecu": _text(source["record"].get("assigned_ecu")),
        "target_assigned_ecu": _text(target["record"].get("assigned_ecu")),
        "source_product_areas": _text(source["service_function"]),
        "target_product_areas": _text(target["service_function"]),
        "service_function": _candidate_service_function(source, target),
        "generation_path": f"{source['host']} -> {target['host']}",
        "lag_days": lag_days,
        "title_similarity": 1.0 if exact_title else None,
        "candidate_kind": kind,
        "candidate_evidence": [evidence],
        "relation_references": [relation_reference] if relation_reference else [],
        "review_priority": "P1" if kind == "direct_relation_reference" else "P2",
        "review_status": REVIEW_STATUS,
        "approved_rule": False,
    }


def _combine_candidate_evidence(
    candidate: dict[str, Any],
    *,
    kind: str,
    evidence: str,
    relation_reference: str = "",
) -> None:
    if evidence not in candidate["candidate_evidence"]:
        candidate["candidate_evidence"].append(evidence)
    if relation_reference and relation_reference not in candidate["relation_references"]:
        candidate["relation_references"].append(relation_reference)
    if candidate["candidate_kind"] != kind:
        candidate["candidate_kind"] = "direct_relation_reference_and_exact_normalized_title"
        candidate["review_priority"] = "P1"
    candidate["title_similarity"] = 1.0


def build_all_function_recurrence_analysis(
    records: Sequence[Mapping[str, object]],
    *,
    minimum_lag_days: int = 14,
    source: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Build high-precision cross-generation review candidates for all functions.

    Candidates are intentionally limited to a shared relationship reference or an
    exact normalized title within a shared, non-broad product function. They are
    review items, never verified duplicate, root-cause, repair, or occurrence claims.
    """
    prepared = _prepared_records(records)
    records_by_function: dict[str, list[dict[str, Any]]] = defaultdict(list)
    records_by_relation: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in prepared:
        if record["service_function"]:
            records_by_function[record["service_function"]].append(record)
        if record["relation_reference"]:
            records_by_relation[record["relation_reference"]].append(record)

    function_catalog: list[dict[str, Any]] = []
    shared_functions: set[str] = set()
    for service_function, members in records_by_function.items():
        host_counts = Counter(member["host"] for member in members)
        if sum(bool(host_counts.get(host)) for host in HOSTS) < 2:
            continue
        shared_functions.add(service_function)
        function_catalog.append(
            {
                "service_function": service_function,
                "mgu_ticket_count": int(host_counts["MGU"]),
                "idc_ticket_count": int(host_counts["IDC"]),
                "idcevo_ticket_count": int(host_counts["IDCEVO"]),
                "generation_count": sum(bool(host_counts.get(host)) for host in HOSTS),
                "semantic_matching_allowed": not _is_broad_function_area(service_function),
                "candidate_count": 0,
                "review_status": REVIEW_STATUS,
            }
        )

    candidates_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    for service_function in shared_functions:
        if _is_broad_function_area(service_function):
            continue
        exact_title_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records_by_function[service_function]:
            if len(record["normalized_title"]) >= 12:
                exact_title_groups[record["normalized_title"]].append(record)
        for members in exact_title_groups.values():
            for source_host, target_host in HOST_TRANSITIONS:
                sources = [member for member in members if member["host"] == source_host]
                targets = [member for member in members if member["host"] == target_host]
                for source_record in sources:
                    for target_record in targets:
                        if (target_record["creation_time"] - source_record["creation_time"]).days < minimum_lag_days:
                            continue
                        key = _candidate_key(source_record, target_record)
                        evidence = "Exact normalized title + shared function"
                        if key not in candidates_by_pair:
                            candidates_by_pair[key] = _new_candidate(
                                source_record,
                                target_record,
                                kind="exact_normalized_title_in_shared_function",
                                evidence=evidence,
                            )
                        else:
                            _combine_candidate_evidence(
                                candidates_by_pair[key],
                                kind="exact_normalized_title_in_shared_function",
                                evidence=evidence,
                            )

    for relation_reference, members in records_by_relation.items():
        for source_host, target_host in HOST_TRANSITIONS:
            sources = [member for member in members if member["host"] == source_host]
            targets = [member for member in members if member["host"] == target_host]
            for source_record in sources:
                for target_record in targets:
                    if (target_record["creation_time"] - source_record["creation_time"]).days < minimum_lag_days:
                        continue
                    key = _candidate_key(source_record, target_record)
                    evidence = "Shared relation reference"
                    if key not in candidates_by_pair:
                        candidates_by_pair[key] = _new_candidate(
                            source_record,
                            target_record,
                            kind="direct_relation_reference",
                            evidence=evidence,
                            relation_reference=relation_reference,
                        )
                    else:
                        _combine_candidate_evidence(
                            candidates_by_pair[key],
                            kind="direct_relation_reference",
                            evidence=evidence,
                            relation_reference=relation_reference,
                        )

    candidates = sorted(
        candidates_by_pair.values(),
        key=lambda item: (
            {"P1": 0, "P2": 1}.get(str(item["review_priority"]), 2),
            str(item["service_function"]),
            -int(item["lag_days"]),
            str(item["source_ticket_id"]),
            str(item["target_ticket_id"]),
        ),
    )
    candidate_count_by_function = Counter(candidate["service_function"] for candidate in candidates)
    for catalog_entry in function_catalog:
        catalog_entry["candidate_count"] = int(
            candidate_count_by_function[catalog_entry["service_function"]]
        )
    function_catalog.sort(
        key=lambda item: (
            -int(item["candidate_count"]),
            -int(item["generation_count"]),
            str(item["service_function"]),
        )
    )
    host_counts = Counter(record["host"] for record in prepared)
    candidate_kind_counts = Counter(candidate["candidate_kind"] for candidate in candidates)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source": dict(source or {}),
        "method": {
            "scope": "All functions with at least two explicit host generations",
            "host_resolution": "tproject first, then project",
            "function_dimension": "Atomic product_areas value; slash and comma are preserved as function-name content",
            "minimum_forward_lag_days": int(minimum_lag_days),
            "candidate_channels": [
                "shared relation reference",
                "exact normalized title within shared non-broad function",
            ],
            "excluded_broad_function_labels": sorted(BROAD_FUNCTION_AREAS),
            "prediction_boundary": "This is a high-precision review queue, not a calibrated future-occurrence probability model.",
        },
        "summary": {
            "comparable_ticket_count": len(prepared),
            "mgu_ticket_count": int(host_counts["MGU"]),
            "idc_ticket_count": int(host_counts["IDC"]),
            "idcevo_ticket_count": int(host_counts["IDCEVO"]),
            "shared_function_count": len(function_catalog),
            "all_three_generation_function_count": sum(
                item["generation_count"] == 3 for item in function_catalog
            ),
            "broad_function_context_count": sum(
                not item["semantic_matching_allowed"] for item in function_catalog
            ),
            "candidate_count": len(candidates),
            "candidate_ticket_count": len(
                {
                    ticket_id
                    for candidate in candidates
                    for ticket_id in (candidate["source_ticket_id"], candidate["target_ticket_id"])
                }
            ),
            "candidate_count_by_kind": dict(sorted(candidate_kind_counts.items())),
            "candidate_count_by_transition": dict(
                sorted(Counter(candidate["generation_path"] for candidate in candidates).items())
            ),
        },
        "governance": {
            "candidate_status": REVIEW_STATUS,
            "root_cause_claim": "not established",
            "repair_claim": "not established",
            "future_occurrence_probability": "not calculated",
        },
        "function_catalog": function_catalog,
        "candidates": candidates,
    }


def load_all_function_records(db_path: str | Path) -> tuple[list[dict[str, object]], dict[str, object]]:
    path = Path(db_path).resolve()
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        records = [
            dict(row)
            for row in connection.execute(
                """
                SELECT defect_id, project, tproject, creation_time, name,
                       product_areas, assigned_ecu, aida_businesskey, relation_to,
                       software_version, severity
                FROM octane_defects
                WHERE project IN ('MGU', 'IDC', 'IDCEVO')
                """
            )
        ]
        max_creation_time = connection.execute(
            """
            SELECT MAX(creation_time)
            FROM octane_defects
            WHERE project IN ('MGU', 'IDC', 'IDCEVO')
            """
        ).fetchone()[0]
    finally:
        connection.close()
    return records, {
        "database_path": str(path),
        "raw_ticket_count": len(records),
        "max_creation_time": _text(max_creation_time),
    }


def write_analysis_json(analysis: Mapping[str, object], output_path: str | Path) -> Path:
    path = Path(output_path)
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite an existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _write_sheet(
    workbook: Workbook,
    template_sheet: Any,
    headers: list[str],
    rows: list[list[Any]],
    widths: list[int],
) -> Any:
    sheet = workbook.create_sheet(template_sheet.title)
    sheet.sheet_view.showGridLines = False
    sheet.sheet_view.zoomScale = 85
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A3
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.print_title_rows = "1:1"
    sheet.freeze_panes = "A2"
    for column_index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(column_index)].width = width
    for row_index, values in enumerate([headers, *rows], start=1):
        reference = template_sheet.cell(1 if row_index == 1 else 2, 1)
        required_lines = 1
        for column_index, value in enumerate(values, start=1):
            cell = sheet.cell(row_index, column_index, value)
            if isinstance(value, str):
                cell.data_type = "s"
            cell.font = copy(reference.font)
            cell.fill = copy(reference.fill)
            cell.border = copy(reference.border)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if row_index > 1 and row_index % 2 == 0:
                cell.fill = PatternFill("solid", fgColor="F3F6FA")
            if isinstance(value, datetime):
                cell.number_format = "yyyy-mm-dd hh:mm"
            if row_index > 1 and isinstance(value, str):
                width = max(1, widths[column_index - 1] - 2)
                lines = sum(
                    max(
                        1,
                        math.ceil(
                            sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in line)
                            / width
                        ),
                    )
                    for line in value.split("\n")
                )
                required_lines = max(required_lines, lines)
        sheet.row_dimensions[row_index].height = 32 if row_index == 1 else min(150, max(30, required_lines * 16 + 8))
    last_column = get_column_letter(len(headers))
    sheet.auto_filter.ref = f"A1:{last_column}{sheet.max_row}"
    sheet.print_area = f"A1:{last_column}{sheet.max_row}"
    return sheet


def _review_focus(candidate: Mapping[str, object]) -> str:
    function = _text(candidate.get("service_function"))
    return (
        f"Validate the same trigger, software state, configuration, and vehicle coverage for {function}; "
        "review the original ticket evidence before treating this as a shared issue."
    )


def export_all_function_recurrence_excel(
    *,
    analysis_json: str | Path,
    template_xlsx: str | Path,
    output_xlsx: str | Path,
) -> Path:
    analysis_path = Path(analysis_json)
    template_path = Path(template_xlsx)
    output_path = Path(output_xlsx)
    if output_path.exists():
        raise FileExistsError(f"Refusing to overwrite an existing file: {output_path}")
    payload = json.loads(analysis_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported all-function recurrence analysis schema")
    candidates = list(payload.get("candidates") or [])
    catalog = list(payload.get("function_catalog") or [])
    summary = dict(payload.get("summary") or {})
    if len(candidates) != int(summary.get("candidate_count") or 0):
        raise ValueError("Candidate count differs from the analysis summary")

    scenario_rows: list[list[Any]] = []
    found_rows: list[list[Any]] = []
    card_rows: list[list[Any]] = []
    mapping_rows: list[list[Any]] = []
    candidates_by_function: dict[str, list[tuple[str, Mapping[str, object]]]] = defaultdict(list)
    for index, candidate in enumerate(candidates, start=1):
        scenario_id = f"XGEN-R-{index:04d}"
        service_function = _text(candidate.get("service_function"))
        evidence = " + ".join(str(item) for item in candidate.get("candidate_evidence") or [])
        scenario_rows.append([
            service_function,
            scenario_id,
            _text(candidate.get("target_title")),
            _text(candidate.get("generation_path")),
            _text(candidate.get("source_ticket_id")),
            _text(candidate.get("target_ticket_id")),
            int(candidate.get("lag_days") or 0),
            candidate.get("title_similarity"),
            evidence,
            _text(candidate.get("review_priority")),
            _text(candidate.get("review_status")),
            _review_focus(candidate),
            2,
        ])
        card_rows.append([
            service_function,
            scenario_id,
            _text(candidate.get("target_title")),
            _text(candidate.get("generation_path")),
            _review_focus(candidate),
            "Verify raw relation/reference meaning, trigger, configuration, software state, and vehicle applicability.",
            "Candidate evidence only; not a verified common root cause, repair, or probability.",
        ])
        candidates_by_function[service_function].append((scenario_id, candidate))
        for role, role_name in (("source", "Earlier"), ("target", "Later")):
            ticket_id = _text(candidate.get(f"{role}_ticket_id"))
            host = _text(candidate.get(f"{role}_host"))
            found_rows.append([
                service_function,
                scenario_id,
                _text(candidate.get("target_title")),
                host,
                1,
                ticket_id,
                role_name,
            ])
            mapping_rows.append([
                ticket_id,
                scenario_id,
                service_function,
                host,
                role_name,
                _excel_utc(candidate.get(f"{role}_creation_time")),
                _text(candidate.get(f"{role}_title")),
                _text(candidate.get(f"{role}_assigned_ecu")),
                _text(candidate.get(f"{role}_product_areas")),
                _text(candidate.get("candidate_kind")),
                _text(candidate.get("review_status")),
            ])

    function_rows = [
        [
            _text(item.get("service_function")),
            int(item.get("mgu_ticket_count") or 0),
            int(item.get("idc_ticket_count") or 0),
            int(item.get("idcevo_ticket_count") or 0),
            int(item.get("generation_count") or 0),
            "Yes" if item.get("semantic_matching_allowed") else "Broad context only",
            int(item.get("candidate_count") or 0),
            _text(item.get("review_status")),
        ]
        for item in catalog
    ]
    tree_rows: list[list[Any]] = []
    node_rows: list[list[Any]] = []
    for index, (service_function, function_candidates) in enumerate(sorted(candidates_by_function.items()), start=1):
        tree_id = f"XGEN-DT-{index:03d}"
        ticket_ids = sorted(
            {
                _text(candidate.get(field_name))
                for _, candidate in function_candidates
                for field_name in ("source_ticket_id", "target_ticket_id")
            }
        )
        first_node_row = len(node_rows) + 2
        tree_rows.append([
            service_function,
            tree_id,
            len(function_candidates),
            len(ticket_ids),
            1 + 3 * len(function_candidates),
            "Review route only; do not treat candidate evidence as an approved diagnostic rule.",
            f"05_Decision_Tree_Nodes!A{first_node_row}",
        ])
        node_rows.append([
            service_function,
            tree_id,
            "START",
            "",
            "Entry",
            service_function,
            "Shared all-function scope",
            "",
            "; ".join(ticket_ids),
            REVIEW_STATUS,
        ])
        for scenario_id, candidate in function_candidates:
            for node_type, suffix, parent, action, evidence_source in (
                ("Symptom", "SYM", "START", _text(candidate.get("target_title")), "Ticket titles"),
                ("Evidence Review", "EVID", f"{scenario_id}-SYM", "Verify raw relationship and functional context.", " + ".join(candidate.get("candidate_evidence") or [])),
                ("Regression Review", "REVIEW", f"{scenario_id}-EVID", _review_focus(candidate), "Human review required"),
            ):
                node_rows.append([
                    service_function,
                    tree_id,
                    f"{scenario_id}-{suffix}",
                    parent,
                    node_type,
                    action,
                    evidence_source,
                    scenario_id,
                    f"{candidate['source_ticket_id']}; {candidate['target_ticket_id']}",
                    REVIEW_STATUS,
                ])

    source = dict(payload.get("source") or {})
    generated_at = _excel_utc(payload.get("generated_at"))
    overview_rows = [
        ["Conclusion", "All shared functional areas were scanned. Candidate rows require explicit evidence and remain human-review-only."],
        ["Comparable tickets", int(summary.get("comparable_ticket_count") or 0)],
        ["MGU tickets", int(summary.get("mgu_ticket_count") or 0)],
        ["IDC tickets", int(summary.get("idc_ticket_count") or 0)],
        ["IDCEVO tickets", int(summary.get("idcevo_ticket_count") or 0)],
        ["Shared functions", int(summary.get("shared_function_count") or 0)],
        ["All-three-generation functions", int(summary.get("all_three_generation_function_count") or 0)],
        ["Broad-context functions", int(summary.get("broad_function_context_count") or 0)],
        ["Review candidates", int(summary.get("candidate_count") or 0)],
        ["Candidate tickets", int(summary.get("candidate_ticket_count") or 0)],
        ["Candidate evidence channels", "; ".join(f"{key}: {value}" for key, value in dict(summary.get("candidate_count_by_kind") or {}).items())],
        ["Generation transitions", "; ".join(f"{key}: {value}" for key, value in dict(summary.get("candidate_count_by_transition") or {}).items())],
        ["Minimum forward lag (days)", int(dict(payload.get("method") or {}).get("minimum_forward_lag_days") or 0)],
        ["Prediction boundary", _text(dict(payload.get("method") or {}).get("prediction_boundary"))],
        ["Analysis generated UTC", generated_at],
        ["Source latest ticket UTC", _excel_utc(source.get("max_creation_time"))],
        ["Raw source ticket count", int(source.get("raw_ticket_count") or 0)],
        ["Data source", _text(source.get("database_path"))],
        ["Analysis JSON", analysis_path.resolve().as_posix()],
        ["Style template only", template_path.resolve().as_posix()],
    ]
    quality_rows = [
        ["Comparable tickets", int(summary.get("comparable_ticket_count") or 0), "Host resolved from tproject first, then project; records missing required fields are excluded."],
        ["Shared functions", int(summary.get("shared_function_count") or 0), "Atomic product_areas values present in at least two host generations."],
        ["All-three-generation functions", int(summary.get("all_three_generation_function_count") or 0), "Functions with MGU, IDC, and IDCEVO coverage."],
        ["Broad-context functions", int(summary.get("broad_function_context_count") or 0), "BS2, Stability, and Performance are catalogued but do not create title-only candidates."],
        ["Review candidates", int(summary.get("candidate_count") or 0), "Shared relation references or exact normalized titles within a shared non-broad function."],
        ["Candidate tickets", int(summary.get("candidate_ticket_count") or 0), "Distinct tickets participating in candidate pairs."],
        ["Approval state", REVIEW_STATUS, "No candidate is a verified root cause, repair, or future occurrence probability."],
        ["Source mutation", "None", "The input SQLite database, analysis JSON, and style template are read only."],
    ]
    dictionary_rows = [
        ["Service Function", "Atomic product_areas field value", "Slash and comma remain part of the function name and are not delimiters."],
        ["Generation Path", "Source host to later target host", "Host resolution uses tproject before project; chronology uses ticket creation time."],
        ["Direct Relation Reference", "The same nonblank relation_to value appears in both host populations", "Review the referenced relationship before treating it as a duplicate or causal link."],
        ["Exact Normalized Title", "Titles match after platform/version/noise normalization in the same function", "A high-precision review signal, not proof of a shared root cause."],
        ["Broad Context", "BS2, Stability, or Performance", "Catalogued for coverage but cannot create title-only candidate rows."],
        ["Ticket Created UTC", "Native Excel datetime normalized to UTC", "A forward gap is a review condition, not proof of first occurrence."],
        ["Review Priority", "P1 shared relation reference; P2 exact title and shared function", "Priority is for review ordering only."],
        ["Review Status", REVIEW_STATUS, "No automatic promotion into repair instructions or decision rules."],
        ["Prediction Boundary", "No calibrated supervised probability", "Requires reviewed positive/negative linkage labels and time-split validation."],
    ]
    sheet_specs = {
        "00_\u603b\u89c8": (["All-Function Cross-Generation Recurrence Analysis", ""], overview_rows, [36, 112]),
        "01_Scenario_Library": (["Service Function", "Scenario ID", "Customer Symptom", "Generation Path", "Earlier Ticket ID", "Later Ticket ID", "Gap (Days)", "Title Similarity", "Candidate Evidence", "Review Priority", "Review Status", "Regression Review Focus", "Ticket Count"], scenario_rows, [32, 18, 62, 22, 18, 18, 12, 16, 38, 16, 24, 72, 14]),
        "02_Scenario_FoundIn_Function": (["Service Function", "Scenario ID", "Customer Symptom", "Generation", "Ticket Count", "Ticket ID", "Mapping Role"], found_rows, [32, 18, 62, 16, 14, 18, 18]),
        "03_Service_Function_Catalog": (["Service Function", "MGU Tickets", "IDC Tickets", "IDCEVO Tickets", "Generation Count", "Title Match Policy", "Candidate Count", "Review Status"], function_rows, [64, 14, 14, 16, 18, 24, 18, 24]),
        "04_Decision_Tree_Catalog": (["Service Function", "Tree ID", "Scenario Count", "Unique Tickets", "Node Count", "Review Boundary", "Workbook Navigation"], tree_rows, [32, 22, 18, 18, 16, 72, 42]),
        "05_Decision_Tree_Nodes": (["Service Function", "Tree ID", "Node ID", "Parent Node ID", "Node Type", "Question / Review Action", "Evidence Source", "Scenario ID", "Ticket IDs", "Review Status"], node_rows, [32, 22, 30, 30, 22, 72, 52, 18, 28, 24]),
        "06_Service_Future_Plan_Cards": (["Service Function", "Scenario ID", "Customer Symptom", "Generation Path", "Suggested Regression Review", "Evidence To Verify", "Review Boundary"], card_rows, [32, 18, 62, 22, 72, 64, 72]),
        "07_Ticket_Mapping": (["Ticket ID", "Scenario ID", "Service Function", "Generation", "Pair Role", "Ticket Created UTC", "Ticket Title", "Assigned ECU", "Product Areas", "Candidate Kind", "Review Status"], mapping_rows, [18, 18, 32, 16, 16, 23, 80, 34, 64, 38, 24]),
        "08_Data_Quality": (["Metric", "Count / Value", "Meaning"], quality_rows, [32, 36, 110]),
        "09_Data_Dictionary": (["Field", "Definition", "Governance Rule"], dictionary_rows, [32, 70, 104]),
    }

    template = load_workbook(template_path)
    workbook = Workbook()
    workbook.remove(workbook.active)
    try:
        if set(template.sheetnames) != set(sheet_specs):
            raise ValueError("Template does not match the expected ten-sheet review layout")
        for template_sheet in template.worksheets:
            headers, rows, widths = sheet_specs[template_sheet.title]
            _write_sheet(workbook, template_sheet, headers, rows, widths)
        overview = workbook.worksheets[0]
        overview.merge_cells("A1:B1")
        overview["A1"].font = Font(name=overview["A1"].font.name, size=16, bold=True, color="FFFFFFFF")
        overview["A1"].fill = copy(workbook["01_Scenario_Library"]["A1"].fill)
        overview.row_dimensions[1].height = 38
        overview.auto_filter.ref = None
        overview.freeze_panes = "B2"
        scenarios = workbook["01_Scenario_Library"]
        scenarios.freeze_panes = "D2"
        mapping = workbook["07_Ticket_Mapping"]
        mapping.freeze_panes = "C2"
        for row_index in range(2, scenarios.max_row + 1):
            similarity = scenarios.cell(row_index, 8)
            if similarity.value is not None:
                similarity.number_format = "0.0%"
            for column_index, mapping_row in ((5, (row_index - 2) * 2 + 2), (6, (row_index - 2) * 2 + 3)):
                cell = scenarios.cell(row_index, column_index)
                cell.hyperlink = f"#'07_Ticket_Mapping'!A{mapping_row}"
                cell.font = Font(name=cell.font.name, size=11, color="0563C1", underline="single")
        for row_index in range(2, workbook["04_Decision_Tree_Catalog"].max_row + 1):
            cell = workbook["04_Decision_Tree_Catalog"].cell(row_index, 7)
            cell.hyperlink = f"#{cell.value}"
            cell.font = Font(name=cell.font.name, size=11, color="0563C1", underline="single")
        workbook.properties.title = "All-Function Cross-Generation Recurrence Review"
        workbook.properties.subject = "Cross-generation recurrence review candidates for all shared functions"
        workbook.properties.description = "Human-review-only candidates; no verified root-cause, repair, or occurrence-probability claim."
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("xb") as handle:
            workbook.save(handle)
    finally:
        workbook.close()
        template.close()
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build all-function cross-generation recurrence analysis from the Octane SQLite snapshot.")
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--minimum-lag-days", type=int, default=14)
    arguments = parser.parse_args()
    records, source = load_all_function_records(arguments.db_path)
    analysis = build_all_function_recurrence_analysis(
        records,
        minimum_lag_days=max(0, int(arguments.minimum_lag_days)),
        source=source,
    )
    output = write_analysis_json(analysis, arguments.output_json)
    print(output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())