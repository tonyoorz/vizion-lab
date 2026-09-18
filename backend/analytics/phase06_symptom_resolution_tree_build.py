"""Build Phase 06 symptom-to-resolution-evidence navigation trees.

This module reuses the Phase 09 V12 navigation shape without treating an LLM
RCA assessment as a verified cause or a Ticket-recorded solution.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from backend.analytics.phase06_rca_candidate_library import (
    AUDIT_COLUMNS,
    DEFAULT_INPUTS,
    DEFAULT_SUMMARIES,
    DEFAULT_WORKBOOK,
    build_phase06_rca_candidate_library_from_sources,
)
from backend.analytics.phase09_idcevo_scenario_build import (
    GUIDED_MAX_ISSUE_GROUPS,
    PROBLEM_TYPE_LABELS,
    SYMPTOM_PATTERN_LABELS,
    TRIGGER_PATTERN_LABELS,
    _recorded_evidence_text,
    _guided_symptom_pattern,
    _guided_trigger_pattern,
    build_symptom_solution_trees,
    classify_problem_type,
    classify_service_function,
    render_symptom_solution_tree_catalog_html,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_XLSX = (
    REPO_ROOT
    / "exports"
    / "idcevo_phase06"
    / "IDCEVO_Phase06_Symptom_to_Resolution_Evidence_Trees_V1.xlsx"
)
DEFAULT_OUTPUT_JSON = (
    REPO_ROOT
    / "exports"
    / "idcevo_phase06"
    / "IDCEVO_Phase06_Symptom_to_Resolution_Evidence_Trees_V1_Decision_Tree_Knowledge_Base.json"
)
DEFAULT_OUTPUT_HTML = (
    REPO_ROOT
    / "exports"
    / "idcevo_phase06"
    / "IDCEVO_Phase06_Symptom_to_Resolution_Evidence_Trees_V1_Decision_Tree_Catalog.html"
)
_EMPTY_EVIDENCE_VALUES = {
    "",
    "(blank)",
    "none",
    "n/a",
    "na",
    "not recorded",
    "unknown",
    "not applicable",
    "\u65e0",
    "\u672a\u8bb0\u5f55",
    "\u6682\u65e0",
}
_RESOLUTION_EVIDENCE_FIELDS = (
    ("repair_plan_evidence", "Repair plan evidence"),
    ("decision_evidence", "Decision evidence"),
)
HEADER_FILL = PatternFill("solid", fgColor="164A5C")
HEADER_FONT = Font(color="FFFFFF", bold=True)
HEADER_ALIGNMENT = Alignment(horizontal="center", vertical="center", wrap_text=True)
BODY_ALIGNMENT = Alignment(vertical="top", wrap_text=True)
TICKET_LIBRARY_COLUMNS = (
    ("Ticket ID", "ticket_id"),
    ("Service Function", "service_function"),
    ("Service Mapping Source", "service_function_match_source"),
    ("Issue Group", "problem_type"),
    ("Symptom Pattern", "symptom_pattern"),
    ("Trigger Pattern", "trigger_pattern"),
    ("Ticket Title", "customer_symptom"),
    ("Problem Description Excerpt", "trigger_condition"),
    ("Recorded Resolution Evidence", "recorded_resolution_evidence"),
    ("Resolution Evidence Source", "recorded_resolution_source"),
    ("Repair Plan Evidence", "repair_plan_evidence"),
    ("Decision Evidence", "decision_evidence"),
    ("No-Fix Evidence", "no_fix_evidence"),
    ("Evidence Index", "evidence_index"),
    ("RCA Review Priority", "rca_review_priority"),
    ("RCA Candidate Status", "rca_candidate_status"),
    ("Approved Tree Rule", "approved_tree_rule"),
)
TREE_COLUMNS = (
    ("Service Function", "service_function"),
    ("Ticket Record Count", "scenario_count"),
    ("Ticket Count", "ticket_count"),
    ("Problem Types", "problem_types"),
    ("P1 Review Ticket Count", "p1_review_ticket_count"),
    ("P2 Review Ticket Count", "p2_review_ticket_count"),
    ("Approved Tree Rule Count", "approved_tree_rule_count"),
    ("Tree Generation", "tree_generation"),
    ("Source Status", "source_status"),
)
NODE_COLUMNS = (
    ("Service Function", "service_function"),
    ("Node ID", "node_id"),
    ("Parent Node ID", "parent_node_id"),
    ("Node Type", "node_type"),
    ("Question / Resolution", "question_or_action"),
    ("Evidence Source", "evidence_required"),
    ("Resolution Status", "resolution_status"),
    ("Recorded Resolution Evidence Count", "recorded_resolution_evidence_ticket_count"),
    ("P1 Review Ticket Count", "p1_review_ticket_count"),
    ("P2 Review Ticket Count", "p2_review_ticket_count"),
    ("No-Fix Evidence Ticket Count", "no_fix_evidence_ticket_count"),
    ("Source Ticket Count", "source_ticket_count"),
    ("Source Ticket IDs", "source_ticket_ids"),
    ("Approved Tree Rule", "approved_tree_rule"),
    ("Node Source", "node_source"),
)


def _text(value: object) -> str:
    return str(value or "").strip()


def _has_evidence(value: object) -> bool:
    return _text(value).casefold() not in _EMPTY_EVIDENCE_VALUES


def _resolution_evidence(record: Mapping[str, Any]) -> tuple[str, str]:
    for field_name, source_label in _RESOLUTION_EVIDENCE_FIELDS:
        value = _recorded_evidence_text(record.get(field_name))
        if _has_evidence(value):
            return value, source_label
    return "", ""


def build_phase06_tree_records(
    audit_records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Build V12-shaped Ticket navigation records without materializing RCA claims."""
    tree_records: list[dict[str, Any]] = []
    for audit_record in audit_records:
        ticket_id = _text(audit_record.get("ticket_id"))
        if not ticket_id:
            continue
        customer_symptom = _text(audit_record.get("ticket_title"))
        trigger_condition = _text(audit_record.get("problem_description_excerpt"))
        affected_function = _text(audit_record.get("solution_cluster")) or _text(
            audit_record.get("function")
        )
        classifier_input = {
            "affected_function": affected_function,
            "affected_function_raw": _text(audit_record.get("solution_cluster")),
            "rep_function": _text(audit_record.get("function")),
            "rep_title": customer_symptom,
            "customer_symptom": customer_symptom,
            "trigger_condition": trigger_condition,
            "observed_behaviour": trigger_condition,
            "expected_behaviour": "",
        }
        service_function = classify_service_function(classifier_input)
        problem_type_id = classify_problem_type(classifier_input)
        symptom_pattern_id = _guided_symptom_pattern(classifier_input)
        trigger_pattern_id = _guided_trigger_pattern(classifier_input)
        resolution_evidence, resolution_source = _resolution_evidence(audit_record)
        tree_records.append(
            {
                "scenario_id": ticket_id,
                "ticket_id": ticket_id,
                "ticket_count": 1,
                "affected_function": affected_function,
                "affected_function_raw": _text(
                    audit_record.get("solution_cluster")
                ),
                "rep_function": _text(audit_record.get("function")),
                "rep_title": customer_symptom,
                "customer_symptom": customer_symptom,
                "trigger_condition": trigger_condition,
                "observed_behaviour": trigger_condition,
                "expected_behaviour": "",
                "service_function_id": service_function["service_function_id"],
                "service_function": service_function["service_function"],
                "service_function_label": service_function["display_name"],
                "service_function_match_source": service_function["match_source"],
                "problem_type_id": problem_type_id,
                "problem_type": PROBLEM_TYPE_LABELS[problem_type_id],
                "symptom_pattern_id": symptom_pattern_id,
                "symptom_pattern": SYMPTOM_PATTERN_LABELS[symptom_pattern_id],
                "trigger_pattern_id": trigger_pattern_id,
                "trigger_pattern": TRIGGER_PATTERN_LABELS[trigger_pattern_id],
                "recorded_resolution_evidence": resolution_evidence,
                "recorded_resolution_source": resolution_source,
                "evidence_index": _text(audit_record.get("evidence_index")),
                "repair_plan_evidence": _text(
                    audit_record.get("repair_plan_evidence")
                ),
                "decision_evidence": _text(audit_record.get("decision_evidence")),
                "no_fix_evidence": _text(audit_record.get("no_fix_evidence")),
                "rca_review_priority": _text(audit_record.get("review_priority")),
                "rca_candidate_status": _text(audit_record.get("candidate_status")),
                "approved_tree_rule": False,
            }
        )
    return tree_records


def _ticket_total(records: Sequence[Mapping[str, Any]]) -> int:
    return sum(int(record.get("ticket_count", 0) or 0) for record in records)


def _issue_groups(
    records: Sequence[Mapping[str, Any]],
) -> list[list[dict[str, Any]]]:
    records_by_problem_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        records_by_problem_type[str(record["problem_type_id"])].append(dict(record))
    ordered_groups = sorted(
        records_by_problem_type.values(),
        key=lambda group: (-_ticket_total(group), str(group[0]["problem_type_id"])),
    )
    groups = ordered_groups[:GUIDED_MAX_ISSUE_GROUPS]
    remaining_records = [
        record for group in ordered_groups[GUIDED_MAX_ISSUE_GROUPS:] for record in group
    ]
    if remaining_records:
        groups.append(remaining_records)
    return groups


def _node_ticket_ids(records: Sequence[Mapping[str, Any]], limit: int = 20) -> str:
    ticket_ids = sorted(_text(record.get("ticket_id")) for record in records)
    visible_ticket_ids = ticket_ids[:limit]
    suffix = (
        f"; +{len(ticket_ids) - limit} more"
        if len(ticket_ids) > limit
        else ""
    )
    return "; ".join(visible_ticket_ids) + suffix


def _annotate_node(
    node: dict[str, Any], records: Sequence[Mapping[str, Any]]
) -> None:
    node["source_ticket_ids"] = _node_ticket_ids(records)
    node["p1_review_ticket_count"] = sum(
        record.get("rca_review_priority") == "P1" for record in records
    )
    node["p2_review_ticket_count"] = sum(
        record.get("rca_review_priority") == "P2" for record in records
    )
    node["no_fix_evidence_ticket_count"] = sum(
        _has_evidence(record.get("no_fix_evidence")) for record in records
    )
    node["recorded_resolution_evidence_ticket_count"] = sum(
        _has_evidence(record.get("recorded_resolution_evidence"))
        for record in records
    )
    node["approved_tree_rule"] = False
    node["node_source"] = "phase06_ticket_symptom_resolution_evidence"


def build_phase06_symptom_resolution_trees(
    tree_records: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build V12-shaped Phase 06 navigation trees with review-only RCA metadata."""
    mutable_records = [
        {
            **record,
            "recovery_action": _text(record.get("recorded_resolution_evidence")),
        }
        for record in tree_records
    ]
    trees, nodes, _ = build_symptom_solution_trees(mutable_records, [])
    records_by_service: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in mutable_records:
        records_by_service[str(record["service_function_id"])].append(record)

    issue_records_by_tree_and_index: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for tree in trees:
        tree_id = str(tree["tree_id"])
        tree_records_for_service = records_by_service[str(tree["service_function_id"])]
        for issue_index, issue_records in enumerate(
            _issue_groups(tree_records_for_service), start=1
        ):
            issue_records_by_tree_and_index[(tree_id, issue_index)] = issue_records
        tree["tree_name"] = (
            f"{tree['service_function']} Symptom-to-Resolution Evidence Tree"
        )
        tree.pop("reference_tree_ids", None)
        tree.pop("solution_source_policy", None)
        tree["tree_generation"] = "phase06_ticket_symptom_resolution_evidence_v1"
        tree["diagnostic_layers"] = (
            "Issue Group",
            "Symptom Pattern",
            "Trigger Pattern",
            "Recorded Resolution Evidence",
        )
        tree["resolution_source_policy"] = (
            "Terminal text is copied only from recorded Ticket repair-plan or decision "
            "evidence. It is not a verified repair instruction."
        )
        tree["rca_review_policy"] = (
            "P1/P2 are human-review metadata only. No RCA or repair rule is automatically "
            "approved."
        )
        tree["source_status"] = (
            "V12-shaped navigation from Phase 06 Ticket title and description evidence; "
            "terminal resolution text is shown only when recorded source evidence exists."
        )
        tree["p1_review_ticket_count"] = sum(
            record.get("rca_review_priority") == "P1"
            for record in tree_records_for_service
        )
        tree["p2_review_ticket_count"] = sum(
            record.get("rca_review_priority") == "P2"
            for record in tree_records_for_service
        )
        tree["approved_tree_rule_count"] = 0

    for node in nodes:
        tree_id = str(node["tree_id"])
        node_id_segments = node["node_id"].split("-")
        issue_index_match = (
            node_id_segments[1] if len(node_id_segments) > 1 else ""
        )
        issue_index = int(issue_index_match) if issue_index_match.isdigit() else 0
        supporting_records = records_by_service[
            tree_id.removeprefix("DT-")
        ]
        if node["node_id"] != "START":
            supporting_records = issue_records_by_tree_and_index.get(
                (tree_id, issue_index), []
            )
        if node["node_type"] == "Ticket Solution":
            has_resolution_evidence = bool(node["question_or_action"])
            supporting_records = [
                record
                for record in supporting_records
                if _has_evidence(record.get("recorded_resolution_evidence"))
                == has_resolution_evidence
            ]
            node["node_type"] = "Recorded Resolution Evidence"
            node["resolution_status"] = (
                "ticket_recorded_resolution_evidence"
                if has_resolution_evidence
                else "no_recorded_resolution_evidence"
            )
            node["data_key"] = node["resolution_status"]
            node["evidence_required"] = (
                "Ticket Repair Plan Evidence / Decision Evidence"
                if has_resolution_evidence
                else ""
            )
            node["question_or_action"] = node["question_or_action"].replace(
                "Recorded recovery actions:", "Recorded resolution evidence:"
            )
        else:
            node["resolution_status"] = ""
        _annotate_node(node, supporting_records)
    return trees, nodes


def build_phase06_symptom_resolution_catalog_from_sources(
    workbook_path: str | Path,
    inputs_path: str | Path = DEFAULT_INPUTS,
    summaries_path: str | Path = DEFAULT_SUMMARIES,
) -> dict[str, Any]:
    """Build a Phase 06 navigation catalog from Ticket evidence and review metadata."""
    candidate_library = build_phase06_rca_candidate_library_from_sources(
        workbook_path, inputs_path, summaries_path
    )
    tree_records = build_phase06_tree_records(candidate_library["all_ticket_audit"])
    decision_trees, decision_tree_nodes = build_phase06_symptom_resolution_trees(
        tree_records
    )
    return {
        "source_workbook_name": Path(workbook_path).name,
        "source_audit": candidate_library["source_audit"],
        "validation_audit": candidate_library["validation_audit"],
        "summary": candidate_library["summary"],
        "tree_records": tree_records,
        "review_queue": candidate_library["review_queue"],
        "decision_trees": decision_trees,
        "decision_tree_nodes": decision_tree_nodes,
    }


def _excel_value(value: object) -> object:
    if isinstance(value, str) and len(value) > 32_767:
        suffix = "\n[Truncated at Excel cell limit.]"
        return value[: 32_767 - len(suffix)] + suffix
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    return value


def _style_table(sheet, column_count: int, widths: Sequence[int]) -> None:
    for cell in sheet[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = HEADER_ALIGNMENT
    for row in sheet.iter_rows(min_row=2, max_col=column_count):
        for cell in row:
            cell.alignment = BODY_ALIGNMENT
    sheet.freeze_panes = "A2"
    if sheet.max_row > 1:
        sheet.auto_filter.ref = f"A1:{get_column_letter(column_count)}{sheet.max_row}"
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width


def _write_mapping_sheet(
    workbook: Workbook,
    sheet_name: str,
    columns: Sequence[tuple[str, str]],
    records: Sequence[Mapping[str, Any]],
    widths: Sequence[int],
) -> None:
    sheet = workbook.create_sheet(sheet_name)
    sheet.append([header for header, _ in columns])
    for record in records:
        sheet.append([_excel_value(record.get(key, "")) for _, key in columns])
    _style_table(sheet, len(columns), widths)


def _write_overview_sheet(workbook: Workbook, catalog: Mapping[str, Any]) -> None:
    sheet = workbook.active
    sheet.title = "00_Overview"
    summary = catalog["summary"]
    sheet.append(["IDCEVO Phase 06 Symptom-to-Resolution Evidence Trees"])
    sheet.append([])
    sheet.append(["Source workbook", catalog["source_workbook_name"]])
    sheet.append([
        "Navigation path",
        "Service Function -> Issue Group -> Symptom Pattern -> Trigger Pattern -> Recorded Resolution Evidence",
    ])
    sheet.append([
        "Rule policy",
        "No automatic RCA or repair rules are generated; P1/P2 remains human-review metadata.",
    ])
    sheet.append([])
    sheet.append(["Metric", "Count", "Interpretation"])
    rows = (
        (
            "Phase 06 Ticket records",
            summary["all_ticket_count"],
            "One navigation record per Ticket.",
        ),
        (
            "Validated LLM summaries",
            summary["validated_llm_summary_count"],
            "Used only for P1/P2 review metadata, never as terminal solution text.",
        ),
        (
            "P1 review candidates",
            summary["p1_candidate_count"],
            "Higher-priority human RCA review candidates.",
        ),
        (
            "P2 review candidates",
            summary["p2_candidate_count"],
            "Incomplete or uncertain causal/remedy evidence; human review required.",
        ),
        (
            "Approved tree rules",
            summary["approved_tree_rule_count"],
            "Always zero in generated output.",
        ),
    )
    for row in rows:
        sheet.append(list(row))
    sheet.cell(row=1, column=1).font = Font(bold=True, size=14, color="164A5C")
    for cell in sheet[7]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = HEADER_ALIGNMENT
    for row in sheet.iter_rows(min_row=8, max_col=3):
        for cell in row:
            cell.alignment = BODY_ALIGNMENT
    sheet.column_dimensions["A"].width = 34
    sheet.column_dimensions["B"].width = 18
    sheet.column_dimensions["C"].width = 88


def _write_source_contract_sheet(workbook: Workbook) -> None:
    sheet = workbook.create_sheet("06_Source_Contract")
    sheet.append(["Field", "Layer", "Meaning", "Policy"])
    rows = (
        (
            "Ticket Title / Problem Description Excerpt",
            "Navigation input",
            "Recorded Phase 06 Ticket text used to derive problem navigation patterns.",
            "Classification is a navigation aid, not a live diagnosis.",
        ),
        (
            "Recorded Resolution Evidence",
            "Terminal evidence",
            "Excerpt from recorded repair-plan or decision evidence.",
            "It is not a verified repair instruction or a root-cause conclusion.",
        ),
        (
            "P1 / P2 Review Ticket Count",
            "Review metadata",
            "Counts from the fingerprint-validated Phase 06 RCA candidate library.",
            "They require human review and do not define a decision-tree branch.",
        ),
        (
            "Approved Tree Rule",
            "Governance",
            "Always False in generated output.",
            "An external documented human-review process is required for promotion.",
        ),
    )
    for row in rows:
        sheet.append(list(row))
    _style_table(sheet, 4, [38, 26, 78, 86])


def render_phase06_symptom_resolution_tree_catalog_html(
    trees: list[dict[str, Any]], nodes: list[dict[str, Any]]
) -> str:
    """Render Phase 06 trees with the V12 layout and evidence-only terminal naming."""
    return (
        render_symptom_solution_tree_catalog_html(
            trees,
            nodes,
            reference_tree_catalog=[],
        )
        .replace(
            "IDCEVO Phase 09 Symptom-to-Solution Tree Catalog",
            "Phase 06 Symptom-to-Resolution Evidence Tree Catalog",
        )
        .replace("IDCEVO Phase 09", "Phase 06")
        .replace("Symptom-to-Solution", "Symptom-to-Resolution Evidence")
        .replace("Ticket-recorded Solution", "Recorded Resolution Evidence")
        .replace("Ticket Solution", "Recorded Resolution Evidence")
        .replace("No recorded ticket solution", "No recorded resolution evidence")
        .replace("Source Scenario IDs", "Source Ticket IDs")
        .replace("Mapped Scenarios", "Ticket Records")
        .replace("Guided Nodes", "Navigation Nodes")
        .replace("After-sales reference trees", "Evidence basis")
        .replace(
            "No supplied reference tree; data-derived symptom-to-solution tree only.",
            "Phase 06 Ticket evidence only; no external reference-tree asset is attached.",
        )
        .replace("reference.source_asset", '"#"')
        .replace(
            ".tree-meta { grid-template-columns: repeat(2, minmax(0, 1fr)); } }",
            ".tree-meta { grid-template-columns: repeat(2, minmax(0, 1fr)); } "
            ".tree-meta div:last-child { grid-column: span 2; } }",
        )
        .replace(
            '<section class="controls">',
            '<p class="status">No automatic RCA or repair rules are generated. '
            'P1/P2 counts are human-review metadata only.</p>'
            '<section class="controls">',
        )
        .replace(
            "<th>Source Ticket IDs</th></tr></thead><tbody id=\"node-rows\"></tbody></table>",
            "<th>P1 Review Tickets</th><th>P2 Review Tickets</th>"
            "<th>No-Fix Evidence Tickets</th><th>Source Ticket IDs</th>"
            "</tr></thead><tbody id=\"node-rows\"></tbody></table>",
        )
        .replace(
            "node.source_scenario_count, node.source_ticket_count, node.source_scenario_ids",
            "node.source_scenario_count, node.source_ticket_count, "
            "node.p1_review_ticket_count, node.p2_review_ticket_count, "
            "node.no_fix_evidence_ticket_count, node.source_ticket_ids",
        )
        .replace(" * 84 }; }", " * 156 }; }")
        .replace(
            r'function displayNodeText(node) { let text = String(node.question_or_action || ""); text = text.replace(/^(Observed symptom patterns|Observed trigger patterns|Observed diagnostic context):\s*/u, ""); if (node.node_type === "Technical Evidence") text = text.replace(/; Top ECU:.*$/u, "").replace(/; Top software version:.*$/u, ""); return text; }',
            'function displayNodeText(node) { let text = String(node.question_or_action || ""); '
            'if (node.node_type === "Recorded Resolution Evidence") { '
            'text = text.replace("Recorded resolution evidence: ", ""); '
            'if (!text) return "No recorded resolution evidence"; '
            'const characters = Array.from(text); '
            'return characters.length > 72 ? `${characters.slice(0, 72).join("")}...` : text; '
            '} '
            'text = text.replace(/^(Observed symptom patterns|Observed trigger patterns|Observed diagnostic context):\\s*/u, ""); '
            'if (node.node_type === "Technical Evidence") text = text.replace(/; Top ECU:.*$/u, "").replace(/; Top software version:.*$/u, ""); '
            'return text; }',
        )
    )


def export_phase06_symptom_resolution_tree_catalog(
    catalog: Mapping[str, Any],
    *,
    output_xlsx: str | Path,
    output_json: str | Path,
    output_html: str | Path,
) -> dict[str, Path]:
    """Write a Phase 06 V12-shaped catalog without approving RCA or repair rules."""
    resolved_xlsx = Path(output_xlsx)
    resolved_json = Path(output_json)
    resolved_html = Path(output_html)
    for path in (resolved_xlsx, resolved_json, resolved_html):
        path.parent.mkdir(parents=True, exist_ok=True)

    service_function_by_tree_id = {
        tree["tree_id"]: tree["service_function"]
        for tree in catalog["decision_trees"]
    }
    node_records = [
        {**node, "service_function": service_function_by_tree_id[node["tree_id"]]}
        for node in catalog["decision_tree_nodes"]
    ]
    workbook = Workbook()
    _write_overview_sheet(workbook, catalog)
    _write_mapping_sheet(
        workbook,
        "01_Phase06_Ticket_Library",
        TICKET_LIBRARY_COLUMNS,
        catalog["tree_records"],
        [16, 32, 34, 34, 28, 28, 54, 70, 72, 28, 72, 72, 72, 52, 18, 42, 18],
    )
    _write_mapping_sheet(
        workbook,
        "02_RCA_Review_Queue",
        AUDIT_COLUMNS,
        catalog["review_queue"],
        [14, 42, 24, 18, 16, 28, 26, 26, 34, 52, 68, 82, 82, 64, 64, 64, 16, 24, 22, 24, 64, 64, 64, 48, 16, 18, 18, 18, 18, 18, 18, 18, 18, 72],
    )
    _write_mapping_sheet(
        workbook,
        "03_Service_Function_Catalog",
        TREE_COLUMNS,
        catalog["decision_trees"],
        [34, 18, 18, 56, 22, 22, 26, 38, 80],
    )
    _write_mapping_sheet(
        workbook,
        "04_Decision_Tree_Catalog",
        TREE_COLUMNS,
        catalog["decision_trees"],
        [34, 18, 18, 56, 22, 22, 26, 38, 80],
    )
    _write_mapping_sheet(
        workbook,
        "05_Decision_Tree_Nodes",
        NODE_COLUMNS,
        node_records,
        [32, 22, 22, 30, 78, 44, 38, 26, 22, 22, 28, 22, 70, 20, 38],
    )
    _write_source_contract_sheet(workbook)
    workbook.save(resolved_xlsx)
    workbook.close()

    payload = {
        "schema_version": "phase06-symptom-resolution-tree-kb-v1",
        "tree_generation": (
            "V12-shaped navigation from Service Function through Issue Group, Symptom "
            "Pattern, Trigger Pattern, and Recorded Resolution Evidence."
        ),
        "policy": {
            "resolution_source_policy": (
                "Recorded Resolution Evidence is copied only from Ticket repair-plan or "
                "decision evidence. It is not a verified solution or repair instruction."
            ),
            "rca_policy": (
                "P1/P2 counts and candidate records are human-review metadata only; "
                "LLM root-cause assessments are not tree nodes or automatic rules."
            ),
            "automatic_tree_rule_count": 0,
        },
        "source_workbook_name": catalog["source_workbook_name"],
        "source_audit": catalog.get("source_audit", {}),
        "validation_audit": catalog.get("validation_audit", {}),
        "summary": catalog["summary"],
        "ticket_records": catalog["tree_records"],
        "review_queue": catalog["review_queue"],
        "decision_trees": catalog["decision_trees"],
        "decision_tree_nodes": catalog["decision_tree_nodes"],
    }
    resolved_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    resolved_html.write_text(
        render_phase06_symptom_resolution_tree_catalog_html(
            catalog["decision_trees"], catalog["decision_tree_nodes"]
        ),
        encoding="utf-8",
    )
    return {
        "xlsx": resolved_xlsx,
        "json": resolved_json,
        "html": resolved_html,
    }


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build Phase 06 symptom-to-resolution evidence navigation trees using "
            "Phase 06 Ticket evidence and review-only RCA metadata."
        )
    )
    parser.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    parser.add_argument("--inputs", type=Path, default=DEFAULT_INPUTS)
    parser.add_argument("--summaries", type=Path, default=DEFAULT_SUMMARIES)
    parser.add_argument("--output-xlsx", type=Path, default=DEFAULT_OUTPUT_XLSX)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--output-html", type=Path, default=DEFAULT_OUTPUT_HTML)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    catalog = build_phase06_symptom_resolution_catalog_from_sources(
        args.workbook, args.inputs, args.summaries
    )
    artifacts = export_phase06_symptom_resolution_tree_catalog(
        catalog,
        output_xlsx=args.output_xlsx,
        output_json=args.output_json,
        output_html=args.output_html,
    )
    summary = catalog["summary"]
    print(f"Phase 06 Ticket records: {summary['all_ticket_count']}")
    print(f"Validated LLM summaries: {summary['validated_llm_summary_count']}")
    print(f"P1 review candidates: {summary['p1_candidate_count']}")
    print(f"P2 review candidates: {summary['p2_candidate_count']}")
    print(f"Approved tree rules: {summary['approved_tree_rule_count']}")
    print(f"Saved Excel to: {artifacts['xlsx']}")
    print(f"Saved JSON to: {artifacts['json']}")
    print(f"Saved HTML to: {artifacts['html']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())