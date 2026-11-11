from __future__ import annotations

import argparse
import json
import math
import re
import unicodedata
from collections import defaultdict
from collections.abc import Mapping, Sequence
from copy import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sklearn.feature_extraction.text import TfidfVectorizer


HOST_TRANSITIONS = (
    ("MGU", "IDC"),
    ("IDC", "IDCEVO"),
    ("MGU", "IDCEVO"),
)


def _text(value: object) -> str:
    return str(value or "").strip()


def _matches(pattern: str, value: object) -> bool:
    return re.search(
        rf"(?<![a-z0-9])(?:{pattern})(?![a-z0-9])",
        _text(value).casefold(),
    ) is not None


def infer_explicit_host_generations(record: Mapping[str, object]) -> set[str]:
    text = " ".join(
        _text(record.get(field_name))
        for field_name in (
            "project",
            "tproject",
            "name",
            "software_version",
            "assigned_ecu",
        )
    )
    generations: set[str] = set()
    if _matches(r"mgu(?:18|21|22)?|(?:hu[-_ ]?)?mgu[-_ ]?(?:02[-_ ]?l|01)", text):
        generations.add("MGU")
    if _matches(r"idc(?:23)?|(?:hu[-_ ]?)?mgu[-_ ]?02[-_ ]?a", text):
        generations.add("IDC")
    if _matches(r"idcevo", text):
        generations.add("IDCEVO")
    return generations


def _is_strict_mybmw_record(record: Mapping[str, object]) -> bool:
    assigned_ecu = _text(record.get("assigned_ecu")).casefold()
    title = _text(record.get("name") or record.get("title")).casefold()
    return (
        assigned_ecu.startswith("app_mobile_2_0")
        or "my bmw" in title
        or "mybmw" in title
    )


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


def _product_area_tokens(value: object) -> set[str]:
    return {
        token.strip().casefold()
        for token in re.split(r"[;,|/]", _text(value))
        if token.strip()
    }


def _same_function_evidence(
    source: Mapping[str, object],
    target: Mapping[str, object],
) -> list[str]:
    evidence: list[str] = []
    source_ecu = _text(source.get("assigned_ecu"))
    target_ecu = _text(target.get("assigned_ecu"))
    if source_ecu and source_ecu == target_ecu:
        evidence.append("assigned_ecu")

    source_areas = _product_area_tokens(source.get("product_areas"))
    target_areas = _product_area_tokens(target.get("product_areas"))
    if source_areas & target_areas:
        evidence.append("product_areas")

    source_aida = _text(source.get("aida_businesskey"))
    target_aida = _text(target.get("aida_businesskey"))
    if source_aida and source_aida == target_aida:
        evidence.append("aida_businesskey")
    return evidence


def _single_host_records(records: Sequence[Mapping[str, object]]) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for record in records:
        if not _is_strict_mybmw_record(record):
            continue
        hosts = infer_explicit_host_generations(record)
        if len(hosts) != 1:
            continue
        creation_time = _parse_creation_time(record.get("creation_time"))
        title = _text(record.get("name") or record.get("title"))
        normalized_title = _normalize_title(title)
        if creation_time is None or not normalized_title:
            continue
        prepared.append(
            {
                "record": record,
                "host": next(iter(hosts)),
                "creation_time": creation_time,
                "ticket_id": _text(record.get("defect_id") or record.get("ticket_id")),
                "title": title,
                "normalized_title": normalized_title,
            }
        )
    return prepared


def build_forward_recurrence_candidates(
    records: Sequence[Mapping[str, object]],
    *,
    minimum_lag_days: int = 14,
    minimum_similarity: float = 0.70,
) -> list[dict[str, object]]:
    """Return review candidates, not verified duplicate or root-cause findings."""
    prepared = _single_host_records(records)
    if len(prepared) < 2:
        return []

    vectorizer = TfidfVectorizer(
        analyzer="char",
        ngram_range=(3, 5),
        min_df=1,
        max_features=100_000,
        lowercase=False,
    )
    matrix = vectorizer.fit_transform(
        [item["normalized_title"] for item in prepared]
    )
    by_host: dict[str, list[int]] = {}
    for index, item in enumerate(prepared):
        by_host.setdefault(str(item["host"]), []).append(index)

    candidates: list[dict[str, object]] = []
    for source_host, target_host in HOST_TRANSITIONS:
        source_indices = by_host.get(source_host, [])
        target_indices = by_host.get(target_host, [])
        if not source_indices or not target_indices:
            continue
        similarities = matrix[source_indices] @ matrix[target_indices].T
        for source_offset, source_index in enumerate(source_indices):
            source = prepared[source_index]
            for target_offset, similarity in zip(
                similarities.getrow(source_offset).indices,
                similarities.getrow(source_offset).data,
            ):
                if float(similarity) < minimum_similarity:
                    continue
                target = prepared[target_indices[int(target_offset)]]
                lag_days = (target["creation_time"] - source["creation_time"]).days
                if lag_days < minimum_lag_days:
                    continue
                if source["ticket_id"] and source["ticket_id"] == target["ticket_id"]:
                    continue
                evidence = _same_function_evidence(source["record"], target["record"])
                if not evidence:
                    continue
                candidates.append(
                    {
                        "source_ticket_id": source["ticket_id"],
                        "target_ticket_id": target["ticket_id"],
                        "source_host": source_host,
                        "target_host": target_host,
                        "source_creation_time": source["creation_time"].isoformat(),
                        "target_creation_time": target["creation_time"].isoformat(),
                        "lag_days": lag_days,
                        "similarity": round(float(similarity), 6),
                        "evidence": evidence,
                        "source_title": source["title"],
                        "target_title": target["title"],
                    }
                )
    return sorted(
        candidates,
        key=lambda item: (
            -float(item["similarity"]),
            -int(item["lag_days"]),
            str(item["source_ticket_id"]),
            str(item["target_ticket_id"]),
        ),
    )


def _review_context(candidate: Mapping[str, Any]) -> tuple[str, str, str]:
    title = f"{candidate.get('source_title', '')} {candidate.get('target_title', '')}".casefold()
    if "service partner" in title:
        return (
            "Navigation / Service Partner",
            "\u5bfc\u822a\u81f3\u9996\u9009\u670d\u52a1\u5546\u65f6\u8def\u7ebf\u5361\u7247\u88ab\u622a\u65ad",
            "\u540c\u4e00 App \u7248\u672c\u4e0b\uff0c\u8de8\u4ee3\u8f66\u578b\u5bf9\u6bd4\u9996\u9009\u670d\u52a1\u5546\u5bfc\u822a\u8def\u7ebf\u5361\u7247\u7684\u663e\u793a\u5b8c\u6574\u6027\u3002",
        )
    if "indoor parking" in title:
        return (
            "Indoor Parking",
            "App \u672a\u663e\u793a\u5ba4\u5185\u505c\u8f66\u4fe1\u606f",
            "\u56fa\u5b9a\u8d26\u6237\u548c App \u7248\u672c\uff0c\u8de8\u4ee3\u8f66\u578b\u68c0\u67e5\u5ba4\u5185\u505c\u8f66\u4fe1\u606f\u7684\u83b7\u53d6\u3001\u5237\u65b0\u4e0e\u663e\u793a\u3002",
        )
    if "soc" in title:
        return (
            "Vehicle Status / SOC",
            "\u8f66\u8f86\u56fe\u50cf\u906e\u6321\u5f53\u524d SOC \u6570\u5b57",
            "\u8de8\u4ee3\u8f66\u578b\u5bf9\u6bd4\u8f66\u8f86\u72b6\u6001\u9875\uff0c\u68c0\u67e5\u4e0d\u540c SOC\u3001\u624b\u673a\u5c3a\u5bf8\u548c\u8f66\u578b\u56fe\u7247\u4e0b\u7684\u6587\u5b57\u906e\u6321\u3002",
        )
    if "widget" in title:
        return (
            "Vehicle Widget",
            "\u8f66\u8f86 Widget \u4e2d\u7684\u8f66\u578b\u56fe\u50cf\u88ab\u622a\u65ad",
            "\u540c\u4e00 App \u7248\u672c\u4e0b\uff0c\u8de8\u4ee3\u8f66\u578b\u68c0\u67e5 Widget \u4e2d\u8f66\u578b\u56fe\u50cf\u7684\u7f29\u653e\u3001\u88c1\u5207\u548c\u5e03\u5c40\u3002",
        )
    return (
        "MyBMW App",
        _text(candidate.get("target_title")),
        "\u6838\u5bf9\u540c\u4e00\u529f\u80fd\u3001App \u7248\u672c\u3001\u8d26\u6237\u4e0e\u590d\u73b0\u6761\u4ef6\uff0c\u8865\u5145\u8de8\u4ee3\u8f66\u578b\u56de\u5f52\u3002",
    )


def _excel_utc(value: object) -> datetime | None:
    parsed = _parse_creation_time(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _write_review_sheet(
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
    sheet.sheet_properties.outlinePr.summaryRight = False
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
                line_width = max(1, widths[column_index - 1] - 2)
                line_count = sum(
                    max(1, math.ceil(sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in line) / line_width))
                    for line in value.split("\n")
                )
                required_lines = max(required_lines, line_count)
        sheet.row_dimensions[row_index].height = (
            32 if row_index == 1 else min(150, max(30, required_lines * 16 + 8))
        )
    last_column = get_column_letter(len(headers))
    sheet.auto_filter.ref = f"A1:{last_column}{sheet.max_row}"
    sheet.print_options.horizontalCentered = True
    sheet.print_area = f"A1:{last_column}{sheet.max_row}"
    return sheet


def export_recurrence_excel(
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
    if payload.get("schema_version") != "cross-generation-mybmw-recurrence-v1":
        raise ValueError("Unsupported cross-generation recurrence analysis schema")
    candidates = payload["candidates"]
    summary = payload["summary"]
    if len(candidates) != int(summary["forward_recurrence_candidate_count"]):
        raise ValueError("Candidate count differs from the analysis summary")

    pending = "\u5f85\u4eba\u5de5\u5ba1\u6838"
    boundary = "\u590d\u53d1\u5019\u9009\uff0c\u975e\u5df2\u8bc1\u5b9e\u540c\u6839\u56e0\uff1b\u672a\u8f93\u51fa\u672a\u6765\u53d1\u751f\u6982\u7387\u3002"
    verify = "\u6838\u5bf9 App/\u540e\u7aef\u7248\u672c\u3001\u8d26\u6237\u3001\u8f66\u578b\u3001\u89e6\u53d1\u6761\u4ef6\u548c\u539f\u59cb\u65e5\u5fd7\u3002"
    evidence_labels = {
        "assigned_ecu": "\u540c\u8d23\u4efb ECU",
        "product_areas": "\u529f\u80fd\u57df\u4ea4\u96c6",
        "aida_businesskey": "\u540c\u975e\u7a7a AIDA",
    }
    scenario_rows: list[list[Any]] = []
    found_in_rows: list[list[Any]] = []
    mapping_rows: list[list[Any]] = []
    card_rows: list[list[Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, candidate in enumerate(candidates, start=1):
        scenario_id = f"MYBMW-R-{index:03d}"
        service, symptom, focus = _review_context(candidate)
        transition = f"{candidate['source_host']} -> {candidate['target_host']}"
        ticket_ids = [_text(candidate[f"{role}_ticket_id"]) for role in ("source", "target")]
        evidence = " + ".join(evidence_labels.get(key, key) for key in candidate["evidence"])
        scenario_rows.append([
            service, scenario_id, symptom, transition, *ticket_ids,
            int(candidate["lag_days"]), float(candidate["similarity"]), evidence,
            focus, pending, len(set(ticket_ids)),
        ])
        card_rows.append([service, scenario_id, symptom, transition, focus, verify, boundary])
        grouped[service].append({
            "scenario_id": scenario_id, "symptom": symptom, "focus": focus,
            "transition": transition, "ticket_ids": ticket_ids, "evidence": evidence,
        })
        for role, label in (("source", "Earlier"), ("target", "Later")):
            ticket_id = _text(candidate[f"{role}_ticket_id"])
            host = candidate[f"{role}_host"]
            found_in_rows.append([service, scenario_id, symptom, host, 1, ticket_id, label])
            mapping_rows.append([
                ticket_id, scenario_id, service, host, label,
                _excel_utc(candidate.get(f"{role}_creation_time")),
                _text(candidate.get(f"{role}_title")),
                _text(candidate.get(f"{role}_assigned_ecu")),
                _text(candidate.get(f"{role}_product_areas")), pending,
            ])

    function_rows: list[list[Any]] = []
    tree_rows: list[list[Any]] = []
    node_rows: list[list[Any]] = []
    for index, (service, scenarios) in enumerate(grouped.items(), start=1):
        tree_id = f"MYBMW-DT-{index:02d}"
        all_ids = sorted({ticket_id for scenario in scenarios for ticket_id in scenario["ticket_ids"]})
        transitions = "; ".join(sorted({scenario["transition"] for scenario in scenarios}))
        function_rows.append([service, len(scenarios), len(all_ids), transitions, pending,
                              "\u672a\u6765\u9879\u76ee\u8de8\u4ee3\u9645\u91cd\u70b9\u56de\u5f52\u5019\u9009"])
        first_node_row = len(node_rows) + 2
        tree_rows.append([service, tree_id, len(scenarios), len(all_ids), 1 + 3 * len(scenarios),
                          boundary, f"05_Decision_Tree_Nodes!A{first_node_row}"])
        node_rows.append([service, tree_id, "START", "", "Entry", service,
                          "Title-based functional grouping", "", "; ".join(all_ids), pending])
        for scenario in scenarios:
            scenario_id = scenario["scenario_id"]
            ticket_ids = "; ".join(scenario["ticket_ids"])
            for node_type, suffix, parent, detail, evidence_source in (
                ("Symptom", "SYM", "START", scenario["symptom"], "Recorded ticket titles"),
                ("Evidence Review", "EVID", f"{scenario_id}-SYM", verify, scenario["evidence"]),
                ("Regression Review", "REVIEW", f"{scenario_id}-EVID", scenario["focus"], boundary),
            ):
                node_rows.append([service, tree_id, f"{scenario_id}-{suffix}", parent,
                                  node_type, detail, evidence_source, scenario_id, ticket_ids, pending])

    hosts = summary["single_explicit_host_ticket_count"]
    comparable = sum(int(value) for value in hosts.values())
    ticket_ids = {row[0] for row in mapping_rows}
    source = payload["source"]
    overview_rows = [
        ["\u7ed3\u8bba", "\u5efa\u8bae\u7eb3\u5165\u672a\u6765\u9879\u76ee\u8de8\u4ee3\u9645\u91cd\u70b9\u56de\u5f52\uff1b\u5f53\u524d\u7ed3\u679c\u4ec5\u4e3a\u5f85\u5ba1\u6838\u5019\u9009\u3002"],
        ["\u590d\u53d1\u5019\u9009\u573a\u666f", len(candidates)],
        ["\u5019\u9009\u5173\u8054 Ticket\uff08\u53bb\u91cd\uff09", len(ticket_ids)],
        ["MyBMW \u4e25\u683c\u5165\u9009 Ticket", int(summary["strict_mybmw_ticket_count"])],
        ["\u5355\u4e00\u4e3b\u673a\u6807\u7b7e Ticket", comparable],
        *[[f"{host} \u5355\u4e00\u4e3b\u673a\u6807\u7b7e", int(hosts.get(host, 0))] for host in ("MGU", "IDC", "IDCEVO")],
        ["\u8de8\u4ee3\u8def\u5f84", "; ".join(f"{source_host}->{target_host}: {summary['candidate_count_by_transition'].get(f'{source_host}->{target_host}', 0)}" for source_host, target_host in HOST_TRANSITIONS)],
        ["\u6700\u4f4e\u6807\u9898\u76f8\u4f3c\u5ea6", float(payload["method"]["minimum_title_similarity"])],
        ["\u6700\u5c0f\u5efa\u7968\u95f4\u9694\uff08\u5929\uff09", int(payload["method"]["minimum_forward_lag_days"])],
        ["\u89e3\u91ca\u8fb9\u754c", boundary],
        ["\u76d1\u7763\u9884\u6d4b\u6a21\u578b", "\u5c1a\u4e0d\u5177\u5907\u4e0a\u7ebf\u6761\u4ef6\uff1a\u7f3a\u5c11\u5df2\u5ba1\u6838\u6b63\u8d1f\u6807\u7b7e\u4e0e\u65f6\u95f4\u5916\u9a8c\u8bc1\u3002"],
        ["\u5206\u6790\u751f\u6210\u65f6\u95f4 UTC", _excel_utc(payload.get("generated_at"))],
        ["\u6e90\u6570\u636e\u6700\u65b0\u5efa\u7968 UTC", _excel_utc(source.get("max_creation_time"))],
        ["Excel \u5bfc\u51fa\u65f6\u95f4 UTC", datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)],
        ["\u6570\u636e\u72b6\u6001", "\u5386\u53f2\u5206\u6790\u5bfc\u51fa\uff1b\u672a\u91cd\u65b0\u62c9\u53d6\u6216\u590d\u6838\u5f53\u524d Ticket \u72b6\u6001\u3002"],
        ["\u5206\u6790\u6765\u6e90", analysis_path.resolve().as_posix()],
        ["\u6837\u5f0f\u6a21\u677f\uff08\u4ec5\u683c\u5f0f\uff09", template_path.resolve().as_posix()],
    ]
    quality_rows = [
        ["Source defect rows", int(source["total_defect_rows"]), "All defects in the recorded source snapshot; not a test-exposure denominator."],
        ["Strict MyBMW cohort", int(summary["strict_mybmw_ticket_count"]), "App ECU family or explicit MyBMW title; not all keyword matches."],
        ["Single explicit host", comparable, "Only one explicit host label; not independently verified hardware attribution."],
        ["Multi-host excluded", int(summary["multi_host_records_excluded_from_pairing"]), "Ambiguous or multi-generation records excluded from pair comparison."],
        ["No host label excluded", int(summary["no_explicit_host_records_excluded_from_pairing"]), "No explicit host attribution; exclusion does not imply no recurrence."],
        ["Cohort reconciliation", comparable + int(summary["multi_host_records_excluded_from_pairing"]) + int(summary["no_explicit_host_records_excluded_from_pairing"]), "Must equal the strict MyBMW cohort."],
        ["Candidate pairs", len(candidates), "Similar titles plus a shared dimension and forward date gap; review required."],
        ["Unique candidate tickets", len(ticket_ids), "Distinct ticket IDs, not pair membership rows."],
        ["Title model", "TF-IDF char 3-5 grams", "Lexical title similarity, not semantic equivalence or recurrence probability."],
        ["Review status", pending, "No new RCA verification, current-status refresh, or repair approval performed."],
        ["Test coverage caveat", "MGU / IDC historical evidence", "The historical test export does not establish current IDCEVO coverage or comparable test exposure."],
    ]
    dictionary_rows = [
        ["Service Function", "Title-derived functional grouping", "For navigation only; not a newly verified root cause."],
        ["Scenario ID", "One stable row identifier per candidate pair in this export", "Not a verified merged defect."],
        ["Customer Symptom", "Concise title-based symptom summary", "Original titles are retained in 07_Ticket_Mapping."],
        ["Generation Path", "Explicit source and target host labels", "App ownership and host generation are distinct dimensions."],
        ["Earlier / Later Ticket ID", "The paired source and target records", "Links navigate to their rows in 07_Ticket_Mapping."],
        ["Ticket Created UTC", "Native Excel date value normalized to UTC", "Timezone removed only after UTC conversion; unavailable values remain blank."],
        ["Gap (Days)", "Whole days between recorded creation timestamps", "Not proof of the first-ever defect occurrence."],
        ["Title Similarity", "Character TF-IDF cosine similarity", "Percentage formatting is not a probability of future occurrence."],
        ["Shared Evidence", "Same ECU, overlapping product area, or nonblank AIDA match", "Shared classification alone does not prove the same failure mechanism."],
        ["Regression Focus", "Suggested future cross-generation regression checks", "A review proposal, not a recorded or approved repair plan."],
        ["Review Status", "human_review_required", "Source candidates have not been promoted to confirmed recurrence."],
        ["Ticket Count", "Distinct records associated with the candidate", "Do not sum pair membership counts as unique overall tickets."],
        ["Decision Tree", "Symptom -> evidence review -> regression review", "An engineering-review route, not an approved diagnostic decision tree."],
        ["Data scope", "Historical JSON analysis exported without recalculation", "The Phase 09 workbook supplies presentation only, not this report's evidence."],
    ]
    sheet_specs = {
        "00_\u603b\u89c8": (["MyBMW \u8de8\u4ee3\u9645\u590d\u53d1\u5019\u9009\u5206\u6790", ""], overview_rows, [36, 104]),
        "01_Scenario_Library": (["Service Function", "Scenario ID", "Customer Symptom", "Generation Path", "Earlier Ticket ID", "Later Ticket ID", "Gap (Days)", "Title Similarity", "Shared Evidence", "Regression Focus", "Review Status", "Ticket Count"], scenario_rows, [26, 18, 44, 22, 18, 18, 12, 16, 32, 60, 22, 14]),
        "02_Scenario_FoundIn_Function": (["Service Function", "Scenario ID", "Customer Symptom", "Generation", "Ticket Count", "Ticket IDs", "Mapping Role"], found_in_rows, [26, 18, 44, 16, 14, 18, 18]),
        "03_Service_Function_Catalog": (["Service Function", "Scenario Count", "Unique Tickets", "Generation Coverage", "Review Status", "Recommended Use"], function_rows, [28, 18, 18, 28, 22, 50]),
        "04_Decision_Tree_Catalog": (["Service Function", "Tree ID", "Scenario Count", "Unique Tickets", "Node Count", "Review Boundary", "Workbook Navigation"], tree_rows, [28, 22, 18, 18, 16, 64, 42]),
        "05_Decision_Tree_Nodes": (["Service Function", "Tree ID", "Node ID", "Parent Node ID", "Node Type", "Question / Review Action", "Evidence Source", "Scenario ID", "Ticket IDs", "Review Status"], node_rows, [28, 22, 30, 30, 22, 64, 52, 18, 24, 22]),
        "06_Service_Future_Plan_Cards": (["Service Function", "Scenario ID", "Customer Symptom", "Generation Path", "Suggested Regression Plan (Not Repair)", "Evidence To Verify", "Review Boundary"], card_rows, [28, 18, 44, 22, 64, 52, 64]),
        "07_Ticket_Mapping": (["Ticket ID", "Scenario ID", "Service Function", "Generation", "Pair Role", "Ticket Created UTC", "Ticket Title", "Assigned ECU", "Product Areas", "Review Status"], mapping_rows, [18, 18, 28, 16, 16, 23, 72, 34, 56, 22]),
        "08_Data_Quality": (["Metric", "Count / Value", "Meaning"], quality_rows, [32, 36, 100]),
        "09_Data_Dictionary": (["Field", "Definition", "Governance Rule"], dictionary_rows, [32, 64, 88]),
    }

    template = load_workbook(template_path)
    workbook = Workbook()
    workbook.remove(workbook.active)
    try:
        if set(template.sheetnames) != set(sheet_specs):
            raise ValueError("Template does not match the expected ten-sheet review layout")
        for template_sheet in template.worksheets:
            headers, rows, widths = sheet_specs[template_sheet.title]
            _write_review_sheet(workbook, template_sheet, headers, rows, widths)
        overview = workbook.worksheets[0]
        overview.merge_cells("A1:B1")
        overview["A1"].font = Font(name=overview["A1"].font.name, size=16, bold=True, color="FFFFFFFF")
        overview["A1"].fill = copy(workbook["01_Scenario_Library"]["A1"].fill)
        overview.row_dimensions[1].height = 38
        overview.auto_filter.ref = None
        overview.freeze_panes = "B2"
        overview["B11"].number_format = "0.0%"
        scenarios = workbook["01_Scenario_Library"]
        scenarios.freeze_panes = "D2"
        mapping = workbook["07_Ticket_Mapping"]
        mapping.freeze_panes = "C2"
        for row_index in range(2, scenarios.max_row + 1):
            scenarios.cell(row_index, 8).number_format = "0.0%"
            for column_index, mapping_row in ((5, (row_index - 2) * 2 + 2), (6, (row_index - 2) * 2 + 3)):
                cell = scenarios.cell(row_index, column_index)
                cell.hyperlink = f"#'07_Ticket_Mapping'!A{mapping_row}"
                cell.font = Font(name=cell.font.name, size=11, color="0563C1", underline="single")
        for row_index in range(2, workbook["04_Decision_Tree_Catalog"].max_row + 1):
            cell = workbook["04_Decision_Tree_Catalog"].cell(row_index, 7)
            cell.hyperlink = f"#{cell.value}"
            cell.font = Font(name=cell.font.name, size=11, color="0563C1", underline="single")
        workbook.properties.title = "MyBMW Cross-Generation Recurrence Review"
        workbook.properties.subject = "Historical recurrence candidates and future regression review"
        workbook.properties.description = boundary
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("xb") as handle:
            workbook.save(handle)
    finally:
        workbook.close()
        template.close()
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Export a MyBMW recurrence review using the V12 workbook layout.")
    parser.add_argument("--analysis-json", type=Path, required=True)
    parser.add_argument("--template-xlsx", type=Path, required=True)
    parser.add_argument("--output-xlsx", type=Path, required=True)
    arguments = parser.parse_args()
    output = export_recurrence_excel(
        analysis_json=arguments.analysis_json,
        template_xlsx=arguments.template_xlsx,
        output_xlsx=arguments.output_xlsx,
    )
    print(output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())