#!/usr/bin/env python3
"""Create a review-gated diagnosis-agent knowledge-tree workbook.

The workbook deliberately separates reusable feature scaffolding, historical
observations, and human-approved executable rules. Historical test evidence can
propose a branch, but never promotes a branch into an executable rule itself.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


WORKBOOK_SHEETS = [
    "00_Architecture",
    "01_Feature_Contract",
    "02_Template_Nodes",
    "03_Template_Edges",
    "04_Source_Assets",
    "05_History_Scenarios",
    "06_Ticket_Evidence",
    "07_Growth_Candidates",
    "08_Approval_Queue",
    "09_Coverage_Gaps",
    "10_Data_Dictionary",
]

_HEADER_FILL = PatternFill("solid", fgColor="123C4A")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_THIN_SIDE = Side(style="thin", color="C7D0D6")
_BORDER = Border(
    left=_THIN_SIDE,
    right=_THIN_SIDE,
    top=_THIN_SIDE,
    bottom=_THIN_SIDE,
)
_WRAP = Alignment(wrap_text=True, vertical="top")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_cell_value(value: Any) -> Any:
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return f"'{value}"
    return value


def _template_node(
    feature: dict[str, Any],
    node_id: str,
    node_type: str,
    layer: str,
    question_or_action: str,
    input_contract: str,
    evidence_required: str,
) -> dict[str, str]:
    return {
        "feature_id": _text(feature.get("feature_id")),
        "feature_name": _text(feature.get("feature_name")),
        "node_id": node_id,
        "node_type": node_type,
        "layer": layer,
        "question_or_action": question_or_action,
        "input_contract": input_contract,
        "evidence_required": evidence_required,
        "rule_status": "template_unapproved",
        "execution_status": "not_executable",
        "authority": "feature_template",
        "owner": "feature_team",
        "review_status": "needs_feature_owner_review",
    }


def build_feature_scaffold(
    feature: dict[str, Any],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Return a reusable, non-executable diagnostic scaffold for one feature."""
    if not _text(feature.get("feature_id")):
        raise ValueError("feature_id is required")
    if not _text(feature.get("feature_name")):
        raise ValueError("feature_name is required")

    nodes = [
        _template_node(
            feature,
            "ROOT",
            "entry",
            "intake",
            "Start diagnosis for the reported feature experience",
            "Feature ID; customer or test issue reference",
            "Issue record",
        ),
        _template_node(
            feature,
            "C01_CONTEXT_CAPTURED",
            "decision",
            "intake",
            "Is the customer symptom and intended outcome captured?",
            "Customer symptom; expected behaviour; trigger condition",
            "Reproducible issue description",
        ),
        _template_node(
            feature,
            "A01_CAPTURE_CONTEXT",
            "action",
            "intake",
            "Capture the missing customer, vehicle, feature, and journey context",
            "Customer contact; vehicle identity; feature version; time window",
            "Completed issue intake",
        ),
        _template_node(
            feature,
            "C02_PRECONDITIONS_MET",
            "decision",
            "eligibility",
            "Are the documented feature, vehicle, account, and market preconditions met?",
            "Feature specification; vehicle configuration; market; account state",
            "Recorded precondition check",
        ),
        _template_node(
            feature,
            "R01_PRECONDITION_GUIDANCE",
            "resolution_candidate",
            "eligibility",
            "Provide verified precondition guidance or route to the owning service",
            "Approved feature eligibility rule",
            "Approved rule and verified customer result",
        ),
        _template_node(
            feature,
            "C03_DEPENDENCIES_AVAILABLE",
            "decision",
            "dependency",
            "Are required data, connectivity, entitlement, and upstream services available?",
            "Network state; entitlement; service health; provider response",
            "Timestamped dependency evidence",
        ),
        _template_node(
            feature,
            "A02_CAPTURE_DIAGNOSTIC_EVIDENCE",
            "action",
            "evidence",
            "Capture the diagnostic evidence required to distinguish the dependency path",
            "Logs; traces; software version; session identifiers; screenshots",
            "Attached evidence bundle",
        ),
        _template_node(
            feature,
            "C04_REPRODUCIBLE",
            "decision",
            "reproduction",
            "Can the issue be reproduced using a documented trigger?",
            "Reproduction steps; environment; timestamp",
            "Reproduction result",
        ),
        _template_node(
            feature,
            "C05_SYMPTOM_CLASSIFIED",
            "decision",
            "triage",
            "Can the observed behaviour be classified into a symptom branch?",
            "Observed behaviour; affected capability; customer symptom",
            "Symptom classification rationale",
        ),
        _template_node(
            feature,
            "C06_APPROVED_BRANCH_EXISTS",
            "decision",
            "triage",
            "Does a human-approved diagnostic branch cover this evidence pattern?",
            "Approved branch ID; branch applicability criteria",
            "Approved rule version and matching evidence",
        ),
        _template_node(
            feature,
            "A03_EXECUTE_APPROVED_BRANCH",
            "action",
            "resolution",
            "Execute the approved branch and record the verification result",
            "Approved action; action owner; verification criteria",
            "Execution and verification record",
        ),
        _template_node(
            feature,
            "A04_PROPOSE_BRANCH_FOR_REVIEW",
            "action",
            "growth",
            "Create an evidence-backed candidate branch for test, development, and after-sales review",
            "Historical scenarios; ticket evidence; diagnostic artifacts",
            "Candidate branch with provenance",
        ),
        _template_node(
            feature,
            "E01_ENGINEERING_ESCALATION",
            "escalation",
            "escalation",
            "Escalate with a bounded evidence package when no approved recovery path applies",
            "Issue context; logs; reproduction result; candidate branch ID",
            "Escalation package and owner acknowledgement",
        ),
        _template_node(
            feature,
            "R02_CLOSE_AFTER_VERIFICATION",
            "resolution_candidate",
            "resolution",
            "Close only after the expected customer behaviour is verified",
            "Verification result; customer confirmation when available",
            "Verified recovery evidence",
        ),
    ]
    edges = [
        ("E-ROOT-C01", "ROOT", "C01_CONTEXT_CAPTURED", "NEXT"),
        ("E-C01-C02-TRUE", "C01_CONTEXT_CAPTURED", "C02_PRECONDITIONS_MET", "TRUE"),
        ("E-C01-A01-FALSE", "C01_CONTEXT_CAPTURED", "A01_CAPTURE_CONTEXT", "FALSE"),
        ("E-A01-C02", "A01_CAPTURE_CONTEXT", "C02_PRECONDITIONS_MET", "NEXT"),
        ("E-C02-C03-TRUE", "C02_PRECONDITIONS_MET", "C03_DEPENDENCIES_AVAILABLE", "TRUE"),
        ("E-C02-R01-FALSE", "C02_PRECONDITIONS_MET", "R01_PRECONDITION_GUIDANCE", "FALSE"),
        ("E-C03-C04-TRUE", "C03_DEPENDENCIES_AVAILABLE", "C04_REPRODUCIBLE", "TRUE"),
        ("E-C03-A02-FALSE", "C03_DEPENDENCIES_AVAILABLE", "A02_CAPTURE_DIAGNOSTIC_EVIDENCE", "FALSE"),
        ("E-A02-C04", "A02_CAPTURE_DIAGNOSTIC_EVIDENCE", "C04_REPRODUCIBLE", "NEXT"),
        ("E-C04-C05-TRUE", "C04_REPRODUCIBLE", "C05_SYMPTOM_CLASSIFIED", "TRUE"),
        ("E-C04-E01-FALSE", "C04_REPRODUCIBLE", "E01_ENGINEERING_ESCALATION", "FALSE"),
        ("E-C05-C06-TRUE", "C05_SYMPTOM_CLASSIFIED", "C06_APPROVED_BRANCH_EXISTS", "TRUE"),
        ("E-C05-A04-FALSE", "C05_SYMPTOM_CLASSIFIED", "A04_PROPOSE_BRANCH_FOR_REVIEW", "FALSE"),
        ("E-C06-A03-TRUE", "C06_APPROVED_BRANCH_EXISTS", "A03_EXECUTE_APPROVED_BRANCH", "TRUE"),
        ("E-C06-A04-FALSE", "C06_APPROVED_BRANCH_EXISTS", "A04_PROPOSE_BRANCH_FOR_REVIEW", "FALSE"),
        ("E-A03-R02", "A03_EXECUTE_APPROVED_BRANCH", "R02_CLOSE_AFTER_VERIFICATION", "NEXT"),
        ("E-A04-E01", "A04_PROPOSE_BRANCH_FOR_REVIEW", "E01_ENGINEERING_ESCALATION", "NEXT"),
    ]
    return nodes, [
        {
            "edge_id": edge_id,
            "from_node_id": from_node_id,
            "to_node_id": to_node_id,
            "branch_outcome": branch_outcome,
            "edge_status": "template_unapproved",
            "authority": "feature_template",
        }
        for edge_id, from_node_id, to_node_id, branch_outcome in edges
    ]


def build_growth_candidates(
    feature_id: str, scenario_records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Turn direct field scenarios into review-only branch candidates."""
    candidates = []
    for record in scenario_records:
        if _text(record.get("service_function_id")) != feature_id:
            continue
        scenario_id = _text(record.get("scenario_id"))
        if not scenario_id:
            continue
        recovery_action = _text(
            record.get("recorded_recovery_action")
            or record.get("recovery_action")
        )
        candidates.append({
            "candidate_id": f"GC-{feature_id}-{scenario_id}",
            "feature_id": feature_id,
            "scenario_id": scenario_id,
            "parent_node_id": "C05_SYMPTOM_CLASSIFIED",
            "proposed_node_type": "symptom_branch",
            "proposed_branch_summary": _text(record.get("customer_symptom")),
            "problem_type": _text(record.get("problem_type")),
            "trigger_condition": _text(record.get("trigger_condition")),
            "ticket_count": int(record.get("ticket_count", 0) or 0),
            "representative_ticket_id": _text(record.get("rep_ticket_id")),
            "recorded_recovery_action": recovery_action,
            "recovery_action_status": (
                "recorded_evidence_not_rule" if recovery_action else "no_recorded_recovery"
            ),
            "evidence_confidence": _text(record.get("evidence_confidence")),
            "candidate_status": "evidence_candidate_needs_review",
            "rule_status": "not_an_approved_rule",
            "approval_decision": "",
        })
    return sorted(
        candidates,
        key=lambda candidate: (-candidate["ticket_count"], candidate["scenario_id"]),
    )


def _normalized_header(value: Any) -> str:
    return _text(value).casefold()


def _header_value(
    row: tuple[Any, ...], header_positions: dict[str, int], names: tuple[str, ...]
) -> Any:
    for name in names:
        position = header_positions.get(_normalized_header(name))
        if position is not None and position < len(row):
            return row[position]
    return ""


def _integer(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _load_json_object(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8-sig") as source:
        value = json.load(source)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def _read_sheet_rows(workbook_path: Path, sheet_name: str) -> tuple[list[str], list[tuple[Any, ...]]]:
    workbook = load_workbook(workbook_path, read_only=True, data_only=True)
    try:
        if sheet_name not in workbook.sheetnames:
            raise ValueError(f"Missing {sheet_name} in {workbook_path}")
        worksheet = workbook[sheet_name]
        iterator = worksheet.iter_rows(values_only=True)
        try:
            headers = [_text(value) for value in next(iterator)]
        except StopIteration as error:
            raise ValueError(f"Missing headers in {sheet_name} of {workbook_path}") from error
        return headers, list(iterator)
    finally:
        workbook.close()


def _find_ticket_source_sheet(workbook_path: Path) -> tuple[list[str], list[tuple[Any, ...]]]:
    workbook = load_workbook(workbook_path, read_only=True, data_only=True)
    try:
        preferred_names = ("05_全部明细", "05_All_Tickets")
        candidate_names = [
            *[name for name in preferred_names if name in workbook.sheetnames],
            *[name for name in workbook.sheetnames if name not in preferred_names],
        ]
        for sheet_name in candidate_names:
            worksheet = workbook[sheet_name]
            iterator = worksheet.iter_rows(values_only=True)
            try:
                headers = [_text(value) for value in next(iterator)]
            except StopIteration:
                continue
            normalized_headers = {_normalized_header(header) for header in headers}
            if "ticket id" not in normalized_headers:
                continue
            if not ({"ticket title", "ticket 标题", "title"} & normalized_headers):
                continue
            return headers, list(iterator)
    finally:
        workbook.close()
    raise ValueError(f"No detailed ticket sheet found in {workbook_path}")


def _contains_alias(text: str, alias: str) -> bool:
    normalized_text = text.casefold()
    normalized_alias = alias.casefold().strip()
    if not normalized_alias:
        return False
    if normalized_alias.isascii():
        return bool(
            re.search(
                rf"(?<![a-z0-9]){re.escape(normalized_alias)}(?![a-z0-9])",
                normalized_text,
            )
        )
    return normalized_alias in normalized_text


def _feature_aliases(feature: dict[str, Any]) -> tuple[str, ...]:
    aliases = [_text(feature.get("feature_name"))]
    raw_aliases = feature.get("aliases", [])
    if isinstance(raw_aliases, (list, tuple)):
        aliases.extend(_text(alias) for alias in raw_aliases)
    aliases.append(_text(feature.get("feature_id")).replace("_", " "))
    return tuple(dict.fromkeys(alias for alias in aliases if alias))


def _load_direct_scenarios(
    *,
    scenario_workbook_path: Path,
    knowledge_base_path: Path,
    feature_id: str,
) -> list[dict[str, Any]]:
    knowledge_base = _load_json_object(knowledge_base_path)
    service_function_ids = {
        _text(tree.get("service_function")): _text(tree.get("service_function_id"))
        for tree in knowledge_base.get("decision_trees", [])
        if isinstance(tree, dict)
        and _text(tree.get("service_function"))
        and _text(tree.get("service_function_id"))
    }
    headers, rows = _read_sheet_rows(scenario_workbook_path, "01_Scenario_Library")
    header_positions = {
        _normalized_header(header): index for index, header in enumerate(headers)
    }
    required_headers = ("Service Function", "Scenario ID", "Customer Symptom")
    missing_headers = [
        header
        for header in required_headers
        if _normalized_header(header) not in header_positions
    ]
    if missing_headers:
        raise ValueError(
            "Missing V12 scenario columns: " + ", ".join(missing_headers)
        )

    records = []
    for row in rows:
        service_function = _text(
            _header_value(row, header_positions, ("Service Function",))
        )
        service_function_id = _text(
            _header_value(row, header_positions, ("Service Function ID",))
        ) or service_function_ids.get(service_function, "")
        if service_function_id != feature_id:
            continue
        scenario_id = _text(
            _header_value(row, header_positions, ("Scenario ID",))
        )
        if not scenario_id:
            continue
        records.append({
            "service_function_id": service_function_id,
            "scenario_id": scenario_id,
            "problem_type": _text(
                _header_value(row, header_positions, ("Problem Type",))
            ),
            "customer_symptom": _text(
                _header_value(row, header_positions, ("Customer Symptom",))
            ),
            "trigger_condition": _text(
                _header_value(row, header_positions, ("Trigger Condition",))
            ),
            "ticket_count": _integer(
                _header_value(row, header_positions, ("Ticket Count",))
            ),
            "rep_ticket_id": _text(
                _header_value(row, header_positions, ("Rep Ticket ID",))
            ),
            "recorded_recovery_action": _text(
                _header_value(
                    row,
                    header_positions,
                    ("Recorded Recovery Action", "Recovery Action"),
                )
            ),
            "evidence_confidence": _text(
                _header_value(row, header_positions, ("Evidence Confidence",))
            ),
            "evidence_scope": "direct_feature_field_scenario",
        })
    return sorted(records, key=lambda record: record["scenario_id"])


def _load_direct_ticket_mapping(
    *,
    scenario_workbook_path: Path,
    feature_id: str,
    direct_scenario_ids: set[str],
) -> dict[str, str]:
    """Resolve V12 field-ticket IDs to direct feature Scenario IDs when available."""
    if not direct_scenario_ids:
        return {}
    workbook = load_workbook(scenario_workbook_path, read_only=True, data_only=True)
    try:
        preferred_names = ("07_Ticket_Mapping", "05_Ticket_Mapping")
        sheet_names = [
            *[name for name in preferred_names if name in workbook.sheetnames],
            *[name for name in workbook.sheetnames if name not in preferred_names],
        ]
        for sheet_name in sheet_names:
            worksheet = workbook[sheet_name]
            iterator = worksheet.iter_rows(values_only=True)
            try:
                headers = [_text(value) for value in next(iterator)]
            except StopIteration:
                continue
            header_positions = {
                _normalized_header(header): index
                for index, header in enumerate(headers)
            }
            if {
                _normalized_header("Ticket ID"),
                _normalized_header("Scenario ID"),
            } - set(header_positions):
                continue
            ticket_mapping = {}
            for row in iterator:
                ticket_id = _text(
                    _header_value(row, header_positions, ("Ticket ID",))
                )
                scenario_id = _text(
                    _header_value(row, header_positions, ("Scenario ID",))
                )
                mapped_feature_id = _text(
                    _header_value(row, header_positions, ("Service Function ID",))
                )
                if not ticket_id or scenario_id not in direct_scenario_ids:
                    continue
                if mapped_feature_id and mapped_feature_id != feature_id:
                    continue
                ticket_mapping[ticket_id] = scenario_id
            return ticket_mapping
    finally:
        workbook.close()
    return {}


def _load_ticket_evidence(
    *,
    source_workbook_path: Path,
    feature: dict[str, Any],
    direct_ticket_to_scenario: dict[str, str],
) -> tuple[list[dict[str, str]], int]:
    headers, rows = _find_ticket_source_sheet(source_workbook_path)
    header_positions = {
        _normalized_header(header): index for index, header in enumerate(headers)
    }
    aliases = _feature_aliases(feature)
    ticket_evidence = []
    alias_mention_count = 0
    for row in rows:
        ticket_id = _text(_header_value(row, header_positions, ("Ticket ID",)))
        if not ticket_id:
            continue
        function = _text(_header_value(row, header_positions, ("Function", "功能")))
        ticket_title = _text(
            _header_value(row, header_positions, ("Ticket Title", "Ticket 标题", "Title"))
        )
        description_excerpt = _text(
            _header_value(
                row,
                header_positions,
                ("Description Excerpt", "问题描述摘录", "Description"),
            )
        )
        search_text = " ".join((function, ticket_title, description_excerpt))
        matching_aliases = [
            alias for alias in aliases if _contains_alias(search_text, alias)
        ]
        field_scenario_id = direct_ticket_to_scenario.get(ticket_id, "")
        if matching_aliases:
            alias_mention_count += 1
        if not matching_aliases and not field_scenario_id:
            continue
        ticket_evidence.append({
            "ticket_id": ticket_id,
            "function": function,
            "ticket_title": ticket_title,
            "description_excerpt": description_excerpt,
            "field_scenario_id": field_scenario_id,
            "evidence_scope": (
                "direct_feature_field_ticket_evidence"
                if field_scenario_id
                else "direct_feature_source_ticket_mention"
            ),
            "alias_match": "; ".join(matching_aliases),
        })
    return ticket_evidence, alias_mention_count


def _load_source_assets(
    *, registry_path: Path, manifest_path: Path, feature: dict[str, Any]
) -> list[dict[str, Any]]:
    registry = _load_json_object(registry_path)
    manifest = _load_json_object(manifest_path)
    manifest_by_source = {
        _text(item.get("source_filename")).casefold(): item
        for item in manifest.get("images", [])
        if isinstance(item, dict) and _text(item.get("source_filename"))
    }
    reference_tree_ids = {
        _text(tree_id) for tree_id in feature.get("reference_tree_ids", [])
    }
    assets = []
    for tree in registry.get("trees", []):
        if not isinstance(tree, dict):
            continue
        source_image = _text(tree.get("source_image"))
        manifest_item = manifest_by_source.get(source_image.casefold(), {})
        source_tree_id = _text(tree.get("tree_id"))
        is_adjacent_reference = source_tree_id in reference_tree_ids
        assets.append({
            "source_tree_id": source_tree_id,
            "source_tree_name": _text(tree.get("tree_name_en"))
            or _text(tree.get("tree_name_cn")),
            "source_image": source_image,
            "source_asset": str(
                manifest_path.parent / _text(manifest_item.get("output_filename"))
            )
            if _text(manifest_item.get("output_filename"))
            else "",
            "source_sha256": _text(manifest_item.get("output_sha256")),
            "width": _integer(manifest_item.get("width")),
            "height": _integer(manifest_item.get("height")),
            "structured_graph_status": "requires_human_verified_graph",
            "feature_applicability": (
                "adjacent_workflow_reference_only"
                if is_adjacent_reference
                else "catalog_source_asset"
            ),
            "mapping_rule": (
                "May inform workflow shape only; do not map a branch before graph verification."
                if is_adjacent_reference
                else "Catalog context only; no automatic feature-branch mapping."
            ),
        })
    return sorted(assets, key=lambda asset: asset["source_tree_id"])


def build_diagnosis_agent_workbook_from_files(
    *,
    feature: dict[str, Any],
    scenario_workbook_path: Path,
    knowledge_base_path: Path,
    source_workbook_path: Path,
    registry_path: Path,
    manifest_path: Path,
    output_path: Path,
) -> dict[str, int]:
    """Load the approved input layers and write a review-gated feature workbook."""
    feature_id = _text(feature.get("feature_id"))
    if not feature_id:
        raise ValueError("feature_id is required")
    scenarios = _load_direct_scenarios(
        scenario_workbook_path=scenario_workbook_path,
        knowledge_base_path=knowledge_base_path,
        feature_id=feature_id,
    )
    direct_ticket_to_scenario = _load_direct_ticket_mapping(
        scenario_workbook_path=scenario_workbook_path,
        feature_id=feature_id,
        direct_scenario_ids={scenario["scenario_id"] for scenario in scenarios},
    )
    ticket_evidence, alias_mention_count = _load_ticket_evidence(
        source_workbook_path=source_workbook_path,
        feature=feature,
        direct_ticket_to_scenario=direct_ticket_to_scenario,
    )
    source_assets = _load_source_assets(
        registry_path=registry_path,
        manifest_path=manifest_path,
        feature=feature,
    )
    scaffold_nodes, scaffold_edges = build_feature_scaffold(feature)
    growth_candidates = build_growth_candidates(feature_id, scenarios)
    readiness_metrics = {
        "reference_asset_count": len(source_assets),
        "structured_graph_count": sum(
            asset["structured_graph_status"] == "human_verified"
            for asset in source_assets
        ),
        "direct_scenario_count": len(scenarios),
        "direct_ticket_count": sum(
            scenario["ticket_count"] for scenario in scenarios
        ),
        "source_ticket_mention_count": alias_mention_count,
    }
    write_diagnosis_agent_workbook(
        output_path=output_path,
        feature=feature,
        scaffold_nodes=scaffold_nodes,
        scaffold_edges=scaffold_edges,
        source_assets=source_assets,
        scenario_records=scenarios,
        ticket_evidence=ticket_evidence,
        growth_candidates=growth_candidates,
        readiness_metrics=readiness_metrics,
    )
    return readiness_metrics


def _style_table(worksheet, headers: list[str]) -> None:
    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = (
        f"A1:{get_column_letter(len(headers))}{max(1, worksheet.max_row)}"
    )
    for column_index in range(1, len(headers) + 1):
        header = worksheet.cell(row=1, column=column_index)
        header.font = _HEADER_FONT
        header.fill = _HEADER_FILL
        header.alignment = _WRAP
        header.border = _BORDER
        maximum_length = len(str(header.value or ""))
        for row_index in range(2, worksheet.max_row + 1):
            cell = worksheet.cell(row=row_index, column=column_index)
            cell.alignment = _WRAP
            cell.border = _BORDER
            maximum_length = max(maximum_length, len(str(cell.value or "")))
        worksheet.column_dimensions[get_column_letter(column_index)].width = min(
            max(12, maximum_length + 2), 56
        )


def _write_records(
    workbook: Workbook,
    title: str,
    headers: list[str],
    records: list[dict[str, Any]],
    fields: list[str],
) -> None:
    worksheet = workbook.create_sheet(title)
    worksheet.append(headers)
    for record in records:
        worksheet.append([_safe_cell_value(record.get(field, "")) for field in fields])
    _style_table(worksheet, headers)


def write_diagnosis_agent_workbook(
    *,
    output_path: Path,
    feature: dict[str, Any],
    scaffold_nodes: list[dict[str, Any]],
    scaffold_edges: list[dict[str, Any]],
    source_assets: list[dict[str, Any]],
    scenario_records: list[dict[str, Any]],
    ticket_evidence: list[dict[str, Any]],
    growth_candidates: list[dict[str, Any]],
    readiness_metrics: dict[str, int],
) -> None:
    """Write the reusable diagnosis-agent template and its evidence appendices."""
    workbook = Workbook()
    workbook.remove(workbook.active)

    architecture = workbook.create_sheet("00_Architecture")
    architecture.append(["Diagnosis-Agent Knowledge Tree Architecture"])
    architecture.cell(row=1, column=1).font = Font(bold=True, size=14)
    architecture.append([])
    architecture.append(["Layer", "Purpose", "Governance Rule"])
    architecture.append([
        "Authoritative source image",
        "Preserves supplied after-sales visual assets and their provenance",
        "Requires a human-verified graph before a source branch can be mapped",
    ])
    architecture.append([
        "Feature scaffold",
        "Reusable intake, precondition, dependency, reproduction, triage, and escalation skeleton",
        "Every node starts as template_unapproved and not_executable",
    ])
    architecture.append([
        "Historical observations",
        "Field scenarios and source-ticket mentions from testing history",
        "Evidence can propose a branch but cannot create an executable rule",
    ])
    architecture.append([
        "Approval queue",
        "Feature, test, development, and after-sales reviewers approve, reject, or request evidence",
        "Only an explicit approved decision may release a diagnostic branch",
    ])
    architecture.append([])
    architecture.append(["Metric", "Value", "Interpretation"])
    architecture.append([
        "Reference assets", readiness_metrics.get("reference_asset_count", 0),
        "After-sales images available as source assets",
    ])
    architecture.append([
        "Human-verified source graphs", readiness_metrics.get("structured_graph_count", 0),
        "Only these graphs may receive source-branch mappings",
    ])
    architecture.append([
        "Direct field scenarios", readiness_metrics.get("direct_scenario_count", 0),
        "Direct feature observations eligible to become review candidates",
    ])
    architecture.append([
        "Direct field tickets", readiness_metrics.get("direct_ticket_count", 0),
        "Tickets represented by direct field scenarios",
    ])
    architecture.append([
        "Source-ticket mentions", readiness_metrics.get("source_ticket_mention_count", 0),
        "Direct alias mentions; not necessarily field-scenario evidence",
    ])
    _style_table(architecture, ["Architecture", "", ""])
    architecture.auto_filter.ref = "A3:C7"
    architecture.freeze_panes = "A4"

    feature_record = {
        "feature_id": _text(feature.get("feature_id")),
        "feature_name": _text(feature.get("feature_name")),
        "service_domain": _text(feature.get("service_domain")),
        "customer_job": _text(feature.get("customer_job")),
        "feature_entry_point": _text(feature.get("feature_entry_point")),
        "feature_owner": _text(feature.get("feature_owner")) or "feature_team",
        "source_graph_status": _text(feature.get("source_graph_status"))
        or "no_direct_source_graph",
        "rule_release_gate": "human_approval_required",
        "direct_scenario_count": readiness_metrics.get("direct_scenario_count", 0),
        "direct_ticket_count": readiness_metrics.get("direct_ticket_count", 0),
        "source_ticket_mention_count": readiness_metrics.get("source_ticket_mention_count", 0),
    }
    _write_records(
        workbook,
        "01_Feature_Contract",
        [
            "Feature ID", "Feature Name", "Service Domain", "Customer Job",
            "Feature Entry Point", "Feature Owner", "Source Graph Status",
            "Rule Release Gate", "Direct Scenario Count", "Direct Ticket Count",
            "Source Ticket Mention Count",
        ],
        [feature_record],
        [
            "feature_id", "feature_name", "service_domain", "customer_job",
            "feature_entry_point", "feature_owner", "source_graph_status",
            "rule_release_gate", "direct_scenario_count", "direct_ticket_count",
            "source_ticket_mention_count",
        ],
    )
    _write_records(
        workbook,
        "02_Template_Nodes",
        [
            "Feature ID", "Feature Name", "Node ID", "Node Type", "Layer",
            "Question / Action", "Input Contract", "Evidence Required", "Rule Status",
            "Execution Status", "Authority", "Owner", "Review Status",
        ],
        scaffold_nodes,
        [
            "feature_id", "feature_name", "node_id", "node_type", "layer",
            "question_or_action", "input_contract", "evidence_required", "rule_status",
            "execution_status", "authority", "owner", "review_status",
        ],
    )
    _write_records(
        workbook,
        "03_Template_Edges",
        [
            "Edge ID", "From Node ID", "To Node ID", "Branch Outcome", "Edge Status",
            "Authority",
        ],
        scaffold_edges,
        [
            "edge_id", "from_node_id", "to_node_id", "branch_outcome", "edge_status",
            "authority",
        ],
    )
    _write_records(
        workbook,
        "04_Source_Assets",
        [
            "Source Tree ID", "Source Tree Name", "Source Image", "Source Asset",
            "Image SHA256", "Width", "Height", "Structured Graph Status",
            "Feature Applicability", "Mapping Rule",
        ],
        source_assets,
        [
            "source_tree_id", "source_tree_name", "source_image", "source_asset",
            "source_sha256", "width", "height", "structured_graph_status",
            "feature_applicability", "mapping_rule",
        ],
    )
    _write_records(
        workbook,
        "05_History_Scenarios",
        [
            "Service Function ID", "Scenario ID", "Problem Type", "Customer Symptom",
            "Trigger Condition", "Ticket Count", "Representative Ticket ID",
            "Recorded Recovery Action", "Evidence Confidence", "Evidence Scope",
        ],
        scenario_records,
        [
            "service_function_id", "scenario_id", "problem_type", "customer_symptom",
            "trigger_condition", "ticket_count", "rep_ticket_id",
            "recorded_recovery_action", "evidence_confidence", "evidence_scope",
        ],
    )
    _write_records(
        workbook,
        "06_Ticket_Evidence",
        [
            "Ticket ID", "Function", "Ticket Title", "Description Excerpt",
            "Field Scenario ID", "Evidence Scope", "Alias Match",
        ],
        ticket_evidence,
        [
            "ticket_id", "function", "ticket_title", "description_excerpt",
            "field_scenario_id", "evidence_scope", "alias_match",
        ],
    )
    _write_records(
        workbook,
        "07_Growth_Candidates",
        [
            "Candidate ID", "Feature ID", "Scenario ID", "Parent Node ID",
            "Proposed Node Type", "Proposed Branch Summary", "Problem Type",
            "Trigger Condition", "Ticket Count", "Representative Ticket ID",
            "Recorded Recovery Action", "Recovery Action Status", "Evidence Confidence",
            "Candidate Status", "Rule Status", "Approval Decision",
        ],
        growth_candidates,
        [
            "candidate_id", "feature_id", "scenario_id", "parent_node_id",
            "proposed_node_type", "proposed_branch_summary", "problem_type",
            "trigger_condition", "ticket_count", "representative_ticket_id",
            "recorded_recovery_action", "recovery_action_status", "evidence_confidence",
            "candidate_status", "rule_status", "approval_decision",
        ],
    )
    approval_records = [
        {
            "review_item_id": candidate["candidate_id"],
            "review_item_type": "historical_branch_candidate",
            "target": candidate["scenario_id"],
            "evidence_reference": candidate["representative_ticket_id"],
            "review_status": "pending_human_review",
            "approval_decision": "",
            "required_reviewers": "test; development; after_sales",
            "release_gate": "approved_decision_required",
        }
        for candidate in growth_candidates
    ]
    approval_records.extend(
        {
            "review_item_id": f"SG-{_text(asset.get('source_tree_id'))}",
            "review_item_type": "source_graph_structuring",
            "target": _text(asset.get("source_image")),
            "evidence_reference": _text(asset.get("source_asset")),
            "review_status": _text(asset.get("structured_graph_status"))
            or "requires_human_verified_graph",
            "approval_decision": "",
            "required_reviewers": "after_sales_source_owner",
            "release_gate": "human_verified_source_graph_required",
        }
        for asset in source_assets
        if _text(asset.get("structured_graph_status")) != "human_verified"
    )
    _write_records(
        workbook,
        "08_Approval_Queue",
        [
            "Review Item ID", "Review Item Type", "Target", "Evidence Reference",
            "Review Status", "Approval Decision", "Required Reviewers", "Release Gate",
        ],
        approval_records,
        [
            "review_item_id", "review_item_type", "target", "evidence_reference",
            "review_status", "approval_decision", "required_reviewers", "release_gate",
        ],
    )
    coverage_records = [
        {
            "gap_id": "GAP-SOURCE-GRAPH",
            "gap_type": "source_graph",
            "current_state": str(readiness_metrics.get("structured_graph_count", 0)),
            "required_state": "human_verified source graph before reference branch mapping",
            "recommended_action": "Capture source nodes, directed edges, branch outcomes, and image bounds.",
            "status": "open"
            if readiness_metrics.get("structured_graph_count", 0) == 0
            else "review_required",
        },
        {
            "gap_id": "GAP-APPROVED-BRANCH",
            "gap_type": "approved_feature_branch",
            "current_state": "0",
            "required_state": "approved branch matched to the feature evidence pattern",
            "recommended_action": "Review direct historical candidates before enabling any branch.",
            "status": "open",
        },
    ]
    _write_records(
        workbook,
        "09_Coverage_Gaps",
        [
            "Gap ID", "Gap Type", "Current State", "Required State",
            "Recommended Action", "Status",
        ],
        coverage_records,
        [
            "gap_id", "gap_type", "current_state", "required_state",
            "recommended_action", "status",
        ],
    )
    dictionary_records = [
        {
            "term": "Source asset",
            "layer": "authoritative source",
            "definition": "Supplied after-sales image retained with its filename, hash, and dimensions.",
            "governance": "An image is not a machine-executable graph until its nodes and edges are human verified.",
        },
        {
            "term": "Template scaffold",
            "layer": "feature model",
            "definition": "Reusable diagnosis stages for a new feature.",
            "governance": "Every scaffold node is template_unapproved and not_executable.",
        },
        {
            "term": "Historical branch candidate",
            "layer": "evidence growth",
            "definition": "A proposed symptom branch generated from direct historical field scenarios.",
            "governance": "A candidate is not an approved diagnostic or recovery rule.",
        },
        {
            "term": "Approval decision",
            "layer": "release control",
            "definition": "Explicit reviewer outcome for a candidate branch or source graph.",
            "governance": "Only an explicit approval may release a branch for execution.",
        },
    ]
    _write_records(
        workbook,
        "10_Data_Dictionary",
        ["Term", "Layer", "Definition", "Governance"],
        dictionary_records,
        ["term", "layer", "definition", "governance"],
    )

    if workbook.sheetnames != WORKBOOK_SHEETS:
        raise RuntimeError("Diagnosis-agent workbook sheet order is invalid")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)