#!/usr/bin/env python3
"""Build a Phase 09 evidence overlay for immutable after-sales reference trees."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


REPO_ROOT = Path(__file__).resolve().parents[2]
PHASE09_EXPORT_DIR = REPO_ROOT / "exports" / "idcevo_phase09"
SOURCE_TREE_DIR = PHASE09_EXPORT_DIR / "decision_tree_sources"
DEFAULT_REGISTRY_PATH = SOURCE_TREE_DIR / "tree_registry.json"
DEFAULT_LEGACY_DRAFT_DIRECTORY = SOURCE_TREE_DIR / "extracted"
DEFAULT_V12_WORKBOOK_PATH = (
    PHASE09_EXPORT_DIR
    / "IDCEVO_Phase09_Field_Escape_Scenario_Library_V12_Symptom_to_Solution_Trees.xlsx"
)
DEFAULT_V12_KNOWLEDGE_BASE_PATH = (
    PHASE09_EXPORT_DIR
    / "IDCEVO_Phase09_Field_Escape_Scenario_Library_V12_Symptom_to_Solution_Trees_Decision_Tree_Knowledge_Base.json"
)
DEFAULT_OUTPUT_XLSX = (
    PHASE09_EXPORT_DIR
    / "IDCEVO_Phase09_AfterSales_Reference_Tree_Evidence_Overlay_V1.xlsx"
)
DEFAULT_OUTPUT_JSON = (
    PHASE09_EXPORT_DIR
    / "IDCEVO_Phase09_AfterSales_Reference_Tree_Evidence_Overlay_V1.json"
)
DEFAULT_OUTPUT_HTML = (
    PHASE09_EXPORT_DIR
    / "IDCEVO_Phase09_AfterSales_Reference_Tree_Evidence_Overlay_V1_Catalog.html"
)

SOURCE_GRAPH_NODE_TYPES = {
    "start",
    "decision",
    "action",
    "resolution",
    "escalation",
    "external_route",
    "note",
}
SOURCE_GRAPH_EDGE_OUTCOMES = {"TRUE", "FALSE", "NEXT", "UNKNOWN"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalized_filename(value: Any) -> str:
    return _text(value).replace("\\", "/").rsplit("/", 1)[-1].casefold()


def _ticket_count(scenarios: list[dict[str, Any]]) -> int:
    return sum(int(scenario.get("ticket_count", 0) or 0) for scenario in scenarios)


def _sample_values(values: list[str], limit: int = 3) -> str:
    unique_values = []
    for value in values:
        normalized_value = _text(value)
        if normalized_value and normalized_value not in unique_values:
            unique_values.append(normalized_value)
    shown_values = unique_values[:limit]
    suffix = (
        f"; +{len(unique_values) - limit} more"
        if len(unique_values) > limit
        else ""
    )
    return "; ".join(shown_values) + suffix


def _has_source_bounds(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    try:
        return (
            float(value["x"]) >= 0
            and float(value["y"]) >= 0
            and float(value["width"]) > 0
            and float(value["height"]) > 0
        )
    except (KeyError, TypeError, ValueError):
        return False


def validate_source_graph(graph: dict[str, Any]) -> list[str]:
    """Validate only the facts needed to reproduce a source-tree graph."""
    issues = []
    if not _text(graph.get("tree_id")):
        issues.append("tree_id_missing")
    nodes = graph.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return sorted({*issues, "graph_nodes_missing", "graph_edges_missing"})
    node_ids = []
    for node in nodes:
        if not isinstance(node, dict):
            issues.append("invalid_node_record")
            continue
        node_id = _text(node.get("node_id"))
        if not node_id:
            issues.append("node_id_missing")
        else:
            node_ids.append(node_id)
        if _text(node.get("node_type")) not in SOURCE_GRAPH_NODE_TYPES:
            issues.append("node_type_invalid")
        if not _text(node.get("source_text")):
            issues.append("node_source_text_missing")
        if not _has_source_bounds(node.get("source_bounds")):
            issues.append("node_source_bounds_missing")
    if len(node_ids) != len(set(node_ids)):
        issues.append("node_id_duplicate")

    node_id_set = set(node_ids)
    edges = graph.get("edges")
    if not isinstance(edges, list) or not edges:
        issues.append("graph_edges_missing")
        return sorted(set(issues))
    edge_ids = []
    for edge in edges:
        if not isinstance(edge, dict):
            issues.append("invalid_edge_record")
            continue
        edge_id = _text(edge.get("edge_id"))
        if not edge_id:
            issues.append("edge_id_missing")
        else:
            edge_ids.append(edge_id)
        if _text(edge.get("branch_outcome")).upper() not in SOURCE_GRAPH_EDGE_OUTCOMES:
            issues.append("edge_branch_outcome_invalid")
        if _text(edge.get("from_node_id")) not in node_id_set:
            issues.append("edge_source_not_found")
        if _text(edge.get("to_node_id")) not in node_id_set:
            issues.append("edge_target_not_found")
    if len(edge_ids) != len(set(edge_ids)):
        issues.append("edge_id_duplicate")
    return sorted(set(issues))


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8-sig") as source:
        payload = json.load(source)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def load_v12_scenario_records(
    *, scenario_workbook_path: Path, knowledge_base_path: Path
) -> list[dict[str, Any]]:
    """Load V12 scenarios and resolve their stable Service Function IDs."""
    knowledge_base = _load_json(knowledge_base_path)
    service_function_ids = {
        _text(tree.get("service_function")): _text(tree.get("service_function_id"))
        for tree in knowledge_base.get("decision_trees", [])
        if isinstance(tree, dict)
        and _text(tree.get("service_function"))
        and _text(tree.get("service_function_id"))
    }
    workbook = openpyxl.load_workbook(
        scenario_workbook_path, read_only=True, data_only=True
    )
    try:
        sheet_name = "01_Scenario_Library"
        if sheet_name not in workbook.sheetnames:
            raise ValueError(f"Missing {sheet_name} in {scenario_workbook_path}")
        worksheet = workbook[sheet_name]
        headers = [
            _text(cell.value)
            for cell in next(worksheet.iter_rows(min_row=1, max_row=1))
        ]
        column_by_header = {header: index for index, header in enumerate(headers)}
        required_headers = (
            "Service Function",
            "Scenario ID",
            "Problem Type",
            "Customer Symptom",
            "Trigger Condition",
            "Ticket Count",
            "Recorded Recovery Action",
            "Evidence Confidence",
            "Rep Ticket ID",
        )
        missing_headers = [
            header for header in required_headers if header not in column_by_header
        ]
        if missing_headers:
            raise ValueError(
                f"Missing V12 scenario columns in {scenario_workbook_path}: "
                f"{', '.join(missing_headers)}"
            )
        records = []
        for row in worksheet.iter_rows(min_row=2, values_only=True):
            scenario_id = _text(row[column_by_header["Scenario ID"]])
            if not scenario_id:
                continue
            service_function = _text(row[column_by_header["Service Function"]])
            service_function_id = service_function_ids.get(service_function, "")
            if not service_function_id:
                raise ValueError(
                    f"No service_function_id for {service_function!r} in "
                    f"{knowledge_base_path}"
                )
            records.append({
                "service_function_id": service_function_id,
                "scenario_id": scenario_id,
                "problem_type": _text(row[column_by_header["Problem Type"]]),
                "customer_symptom": _text(
                    row[column_by_header["Customer Symptom"]]
                ),
                "trigger_condition": _text(
                    row[column_by_header["Trigger Condition"]]
                ),
                "ticket_count": int(
                    row[column_by_header["Ticket Count"]] or 0
                ),
                "recorded_solution": _text(
                    row[column_by_header["Recorded Recovery Action"]]
                ),
                "evidence_confidence": _text(
                    row[column_by_header["Evidence Confidence"]]
                ),
                "rep_ticket_id": _text(row[column_by_header["Rep Ticket ID"]]),
            })
        decision_trees = [
            tree
            for tree in knowledge_base.get("decision_trees", [])
            if isinstance(tree, dict)
        ]
        trees_with_scenario_counts = [
            tree for tree in decision_trees if "scenario_count" in tree
        ]
        if trees_with_scenario_counts:
            if len(trees_with_scenario_counts) != len(decision_trees):
                raise ValueError(
                    "V12 scenario count mismatch: decision-tree scenario_count "
                    "metadata is incomplete"
                )
            declared_scenario_count = sum(
                int(tree.get("scenario_count", 0) or 0)
                for tree in trees_with_scenario_counts
            )
            if len(records) != declared_scenario_count:
                raise ValueError(
                    "V12 scenario count mismatch: "
                    f"workbook has {len(records)}, knowledge base declares "
                    f"{declared_scenario_count}"
                )
        return records
    finally:
        workbook.close()


def load_legacy_path_drafts(directory: Path) -> dict[str, dict[str, Any]]:
    """Load existing path drafts only for validation, never for branch mapping."""
    if not directory.is_dir():
        return {}
    drafts = {}
    for draft_path in sorted(directory.glob("*.json")):
        draft = _load_json(draft_path)
        tree_id = _text(draft.get("tree_id"))
        if tree_id:
            drafts[tree_id] = draft
    return drafts


def build_reference_tree_augmentation_from_files(
    *,
    registry_path: Path,
    scenario_workbook_path: Path,
    knowledge_base_path: Path,
    legacy_draft_directory: Path,
) -> dict[str, Any]:
    """Load the source-tree and V12 artifacts required for an evidence overlay."""
    registry = _load_json(registry_path)
    registry_trees = registry.get("trees")
    if not isinstance(registry_trees, list):
        raise ValueError(f"Missing trees list in {registry_path}")
    knowledge_base = _load_json(knowledge_base_path)
    source_catalog = knowledge_base.get("after_sales_reference_trees")
    if not isinstance(source_catalog, list):
        raise ValueError(
            f"Missing after_sales_reference_trees in {knowledge_base_path}"
        )
    scenario_records = load_v12_scenario_records(
        scenario_workbook_path=scenario_workbook_path,
        knowledge_base_path=knowledge_base_path,
    )
    overlay = build_reference_tree_augmentation(
        registry_trees=[
            tree for tree in registry_trees if isinstance(tree, dict)
        ],
        source_catalog=[
            source for source in source_catalog if isinstance(source, dict)
        ],
        scenario_records=scenario_records,
        legacy_drafts=load_legacy_path_drafts(legacy_draft_directory),
    )
    overlay["input_provenance"] = {
        "registry_path": str(registry_path),
        "scenario_workbook_path": str(scenario_workbook_path),
        "knowledge_base_path": str(knowledge_base_path),
        "legacy_draft_directory": str(legacy_draft_directory),
        "scenario_record_count": len(scenario_records),
    }
    return overlay


def validate_legacy_path_draft(draft: dict[str, Any]) -> list[str]:
    """Identify why a path-only draft cannot be used as a source graph."""
    nodes = draft.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return ["missing_nodes", "graph_edges_not_captured"]

    paths = [_text(node.get("node_path")) for node in nodes if isinstance(node, dict)]
    issues = []
    if "edges" not in draft:
        issues.append("graph_edges_not_captured")
    if any(not path for path in paths):
        issues.append("node_path_missing")
    for node in nodes:
        if not isinstance(node, dict):
            issues.append("invalid_node_record")
            continue
        node_path = _text(node.get("node_path"))
        if node.get("is_leaf") and any(
            other_path.startswith(f"{node_path} > ")
            for other_path in paths
            if other_path != node_path
        ):
            issues.append("nonterminal_nodes_marked_leaf")
            break
    if not all(isinstance(node, dict) and _text(node.get("node_id")) for node in nodes):
        issues.append("source_node_ids_not_captured")
    if not all(isinstance(node, dict) and node.get("source_bounds") for node in nodes):
        issues.append("source_locations_not_captured")
    return sorted(set(issues))


def _reference_catalog_by_source(
    source_catalog: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    catalog_by_source = {}
    for source in source_catalog:
        source_filename = _normalized_filename(source.get("source_file"))
        if source_filename:
            catalog_by_source[source_filename] = source
    return catalog_by_source


def build_phase09_data_support(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Aggregate source-tree support without claiming a source-branch match."""
    by_problem_type: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        by_problem_type[(
            _text(candidate.get("tree_id")),
            _text(candidate.get("problem_type")) or "Unclassified",
        )].append(candidate)

    support_rows = []
    for (_, _), grouped_candidates in by_problem_type.items():
        first_candidate = grouped_candidates[0]
        confidence_counts: dict[str, int] = defaultdict(int)
        for candidate in grouped_candidates:
            confidence_counts[_text(candidate.get("evidence_confidence")) or "unknown"] += 1
        with_solution = [
            candidate
            for candidate in grouped_candidates
            if _text(candidate.get("recorded_solution"))
        ]
        support_rows.append({
            "tree_id": first_candidate["tree_id"],
            "reference_tree_id": first_candidate["reference_tree_id"],
            "service_function_id": first_candidate["service_function_id"],
            "problem_type": first_candidate["problem_type"] or "Unclassified",
            "scenario_count": len(grouped_candidates),
            "ticket_count": _ticket_count(grouped_candidates),
            "high_confidence_scenario_count": confidence_counts["high"],
            "medium_confidence_scenario_count": confidence_counts["medium"],
            "low_confidence_scenario_count": confidence_counts["low"],
            "recorded_solution_scenario_count": len(with_solution),
            "recorded_solution_ticket_count": _ticket_count(with_solution),
            "sample_customer_symptoms": _sample_values([
                candidate.get("customer_symptom", "")
                for candidate in grouped_candidates
            ]),
            "sample_trigger_conditions": _sample_values([
                candidate.get("trigger_condition", "")
                for candidate in grouped_candidates
            ]),
            "sample_recorded_solutions": _sample_values([
                candidate.get("recorded_solution", "")
                for candidate in grouped_candidates
            ]),
            "mapping_status": "unmapped_pending_branch_verification",
        })
    return sorted(
        support_rows,
        key=lambda item: (
            item["tree_id"],
            -item["ticket_count"],
            item["problem_type"],
        ),
    )


def build_reference_tree_augmentation(
    *,
    registry_trees: list[dict[str, Any]],
    source_catalog: list[dict[str, Any]],
    scenario_records: list[dict[str, Any]],
    legacy_drafts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Attach Phase 09 observations to a source tree without inferring branch links."""
    catalog_by_source = _reference_catalog_by_source(source_catalog)
    scenarios_by_service: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for scenario in scenario_records:
        service_function_id = _text(scenario.get("service_function_id"))
        if service_function_id:
            scenarios_by_service[service_function_id].append(scenario)

    reference_trees = []
    candidates = []
    review_queue = []
    covered_service_function_ids = set()
    for registry_tree in registry_trees:
        tree_id = _text(registry_tree.get("tree_id"))
        source_image = _text(registry_tree.get("source_image"))
        source_reference = catalog_by_source.get(_normalized_filename(source_image), {})
        service_function_id = _text(source_reference.get("service_function_id"))
        if service_function_id:
            covered_service_function_ids.add(service_function_id)
        tree_scenarios = scenarios_by_service.get(service_function_id, [])
        legacy_draft = legacy_drafts.get(tree_id)
        draft_issues = (
            validate_legacy_path_draft(legacy_draft)
            if isinstance(legacy_draft, dict)
            else ["legacy_path_draft_not_available"]
        )
        legacy_draft_status = (
            "invalid_for_graph_mapping"
            if draft_issues
            else "not_approved_for_graph_mapping"
        )
        reference_tree = {
            "tree_id": tree_id,
            "reference_tree_id": _text(source_reference.get("reference_tree_id")),
            "tree_name_cn": _text(registry_tree.get("tree_name_cn")),
            "tree_name_en": _text(registry_tree.get("tree_name_en")),
            "source_image": source_image,
            "source_asset": _text(source_reference.get("source_asset")),
            "source_tree_authority": "after_sales_source_image",
            "source_graph_status": "requires_human_verified_graph",
            "legacy_path_draft_status": legacy_draft_status,
            "legacy_path_draft_issues": draft_issues,
            "service_function_id": service_function_id,
            "tree_version": _text(source_reference.get("tree_version")),
            "phase09_scenario_count": len(tree_scenarios),
            "phase09_ticket_count": _ticket_count(tree_scenarios),
            "branch_mapping_status": "not_started",
        }
        reference_trees.append(reference_tree)
        review_queue.append({
            "tree_id": tree_id,
            "reference_tree_id": reference_tree["reference_tree_id"],
            "source_image": source_image,
            "source_asset": reference_tree["source_asset"],
            "service_function_id": service_function_id,
            "review_status": "requires_human_verified_graph",
            "legacy_path_draft_status": legacy_draft_status,
            "legacy_path_draft_issues": "; ".join(draft_issues),
            "phase09_scenario_count": len(tree_scenarios),
            "phase09_ticket_count": _ticket_count(tree_scenarios),
            "required_structure": (
                "Capture original node IDs, node text, directed edges, branch outcomes, "
                "and source-image bounds; approve before mapping Phase 09 scenarios."
            ),
        })
        for scenario in tree_scenarios:
            candidates.append({
                "tree_id": tree_id,
                "reference_tree_id": reference_tree["reference_tree_id"],
                "service_function_id": service_function_id,
                "reference_branch_id": "",
                "mapping_scope": "service_function_only",
                "mapping_status": "unmapped_pending_branch_verification",
                "scenario_id": _text(scenario.get("scenario_id")),
                "problem_type": _text(scenario.get("problem_type")),
                "customer_symptom": _text(scenario.get("customer_symptom")),
                "trigger_condition": _text(scenario.get("trigger_condition")),
                "ticket_count": int(scenario.get("ticket_count", 0) or 0),
                "recorded_solution": _text(scenario.get("recorded_solution")),
                "evidence_confidence": _text(scenario.get("evidence_confidence")),
                "rep_ticket_id": _text(scenario.get("rep_ticket_id")),
            })

    uncovered_service_function_candidates = [
        {
            "service_function_id": _text(scenario.get("service_function_id")),
            "scenario_id": _text(scenario.get("scenario_id")),
            "problem_type": _text(scenario.get("problem_type")),
            "customer_symptom": _text(scenario.get("customer_symptom")),
            "trigger_condition": _text(scenario.get("trigger_condition")),
            "ticket_count": int(scenario.get("ticket_count", 0) or 0),
            "recorded_solution": _text(scenario.get("recorded_solution")),
            "evidence_confidence": _text(scenario.get("evidence_confidence")),
            "rep_ticket_id": _text(scenario.get("rep_ticket_id")),
            "coverage_status": "no_matching_after_sales_reference_tree",
        }
        for scenario in scenario_records
        if _text(scenario.get("service_function_id"))
        and _text(scenario.get("service_function_id"))
        not in covered_service_function_ids
    ]
    unmapped_scenario_candidates = sorted(
        candidates,
        key=lambda item: (
            item["tree_id"],
            -item["ticket_count"],
            item["scenario_id"],
        ),
    )
    return {
        "schema_version": "phase09-reference-tree-augmentation-v1",
        "data_policy": {
            "source_tree_policy": (
                "Original after-sales source images and human-verified source graphs are "
                "authoritative; Phase 09 data never changes a source tree automatically."
            ),
            "mapping_policy": (
                "Phase 09 scenarios remain unmapped candidates until a human-approved "
                "source graph provides a reference branch ID."
            ),
        },
        "reference_trees": reference_trees,
        "phase09_data_support": build_phase09_data_support(
            unmapped_scenario_candidates
        ),
        "unmapped_scenario_candidates": unmapped_scenario_candidates,
        "uncovered_service_function_candidates": sorted(
            uncovered_service_function_candidates,
            key=lambda item: (
                item["service_function_id"],
                -item["ticket_count"],
                item["scenario_id"],
            ),
        ),
        "graph_structuring_review_queue": sorted(
            review_queue,
            key=lambda item: (-item["phase09_ticket_count"], item["tree_id"]),
        ),
    }


_THIN_SIDE = Side(style="thin", color="C7D0D6")
_BORDER = Border(
    left=_THIN_SIDE,
    right=_THIN_SIDE,
    top=_THIN_SIDE,
    bottom=_THIN_SIDE,
)
_HEADER_FILL = PatternFill("solid", fgColor="123C4A")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_WRAP = Alignment(wrap_text=True, vertical="top")


def _style_header(worksheet, headers: list[str]) -> None:
    for column_index in range(1, len(headers) + 1):
        cell = worksheet.cell(row=1, column=column_index)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = _WRAP
        cell.border = _BORDER


def _style_sheet(worksheet, headers: list[str], freeze_panes: str = "A2") -> None:
    _style_header(worksheet, headers)
    worksheet.freeze_panes = freeze_panes
    worksheet.auto_filter.ref = (
        f"A1:{get_column_letter(len(headers))}{worksheet.max_row}"
    )
    for column_index, header in enumerate(headers, start=1):
        width = min(max(len(header) + 2, 14), 34)
        worksheet.column_dimensions[get_column_letter(column_index)].width = width
    for row in worksheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = _WRAP
            cell.border = _BORDER


def _append_table(worksheet, headers: list[str], rows: list[dict[str, Any]]) -> None:
    worksheet.append(headers)
    for row in rows:
        worksheet.append([row.get(header, "") for header in headers])
    _style_sheet(worksheet, headers)


def render_reference_tree_augmentation_html(overlay: dict[str, Any]) -> str:
    """Render original source images beside their unmapped Phase 09 evidence."""
    html_data = json.dumps(overlay, ensure_ascii=False).replace("<", "\\u003c").replace(
        ">", "\\u003e"
    ).replace("&", "\\u0026")
    template = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Phase 09 After-Sales Reference Tree Evidence Overlay</title>
<style>
:root { color: #18212b; background: #f2f4f5; font-family: Aptos, "Noto Sans SC", "Microsoft YaHei", sans-serif; }
* { box-sizing: border-box; }
body { margin: 0; }
main { max-width: 1680px; margin: 0 auto; padding: 30px 28px 52px; }
header { border-bottom: 4px solid #167a72; padding-bottom: 18px; }
h1 { margin: 0; color: #123c4a; font-size: 28px; letter-spacing: 0; }
p { line-height: 1.5; }
.subtitle { margin: 8px 0 0; color: #52616b; font-size: 15px; }
.controls { margin: 22px 0 16px; }
label { color: #33414b; font-size: 13px; font-weight: 700; }
select { display: block; width: min(540px, 100%); min-height: 42px; margin-top: 7px; padding: 8px 12px; border: 1px solid #7d919d; border-radius: 5px; background: #fff; color: #18212b; font: inherit; }
.source-workspace { display: grid; grid-template-columns: minmax(0, 1.65fr) minmax(280px, .75fr); gap: 18px; align-items: start; }
.source-panel { min-width: 0; border: 1px solid #b9c6cc; background: #fff; }
.source-panel header { display: flex; justify-content: space-between; gap: 12px; align-items: center; border: 0; padding: 12px 14px; background: #e9f0f0; color: #183c4a; font-size: 13px; font-weight: 700; }
.source-panel a { color: #116c6d; text-decoration-thickness: 1px; text-underline-offset: 2px; white-space: nowrap; }
.image-scroll { max-height: 780px; overflow: auto; }
#source-tree-image { display: block; width: 100%; min-width: 920px; height: auto; }
.tree-facts { border-top: 3px solid #167a72; background: #fff; }
.tree-facts dl { margin: 0; }
.tree-facts div { padding: 12px 14px; border: 1px solid #c7d0d6; border-top: 0; }
.tree-facts dt { color: #5a6871; font-size: 12px; }
.tree-facts dd { margin: 5px 0 0; color: #183c4a; font-size: 14px; font-weight: 700; line-height: 1.35; }
.section-title { margin: 28px 0 10px; color: #123c4a; font-size: 18px; }
.table-shell { overflow: auto; border: 1px solid #b9c6cc; background: #fff; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 9px 10px; border: 1px solid #c7d0d6; text-align: left; vertical-align: top; line-height: 1.4; }
th { background: #123c4a; color: #fff; white-space: nowrap; }
td.count { text-align: right; white-space: nowrap; }
.status { color: #80521a; }
@media (max-width: 900px) { main { padding: 22px 16px 36px; } h1 { font-size: 23px; } .source-workspace { grid-template-columns: 1fr; } .image-scroll { max-height: 640px; } }
</style>
</head>
<body>
<main>
  <header>
    <h1>Phase 09 After-Sales Reference Tree Evidence Overlay</h1>
    <p class="subtitle">Original after-sales source tree with Phase 09 evidence support. The source tree is not changed automatically.</p>
  </header>
  <section class="controls"><label for="tree-selector">After-sales source tree<select id="tree-selector"></select></label></section>
  <section class="source-workspace">
    <section class="source-panel" aria-label="Original after-sales source tree">
      <header><span>Original after-sales source tree</span><a id="source-link" target="_blank" rel="noopener">Open original</a></header>
      <div class="image-scroll"><img id="source-tree-image" alt="Original after-sales decision tree"></div>
    </section>
    <aside class="tree-facts"><dl>
      <div><dt>Reference tree</dt><dd id="reference-tree-id"></dd></div>
      <div><dt>Phase 09 scenarios</dt><dd id="scenario-count"></dd></div>
      <div><dt>Phase 09 tickets</dt><dd id="ticket-count"></dd></div>
      <div><dt>Source graph status</dt><dd class="status" id="graph-status"></dd></div>
      <div><dt>Branch mapping status</dt><dd class="status" id="mapping-status"></dd></div>
      <div><dt>Legacy draft status</dt><dd class="status" id="draft-status"></dd></div>
    </dl></aside>
  </section>
  <h2 class="section-title">Phase 09 data support by problem type</h2>
  <section class="table-shell"><table><thead><tr><th>Problem type</th><th>Scenarios</th><th>Tickets</th><th>Recorded-solution tickets</th><th>Sample symptoms</th><th>Sample triggers</th><th>Sample recorded solutions</th><th>Mapping status</th></tr></thead><tbody id="support-rows"></tbody></table></section>
  <h2 class="section-title">Unmapped scenario review candidates</h2>
  <section class="table-shell"><table><thead><tr><th>Scenario</th><th>Problem type</th><th>Customer symptom</th><th>Trigger condition</th><th>Tickets</th><th>Ticket-recorded solution</th><th>Evidence confidence</th><th>Reference branch</th><th>Mapping status</th></tr></thead><tbody id="candidate-rows"></tbody></table></section>
    <h2 class="section-title">Uncovered service-function candidates</h2>
    <section class="table-shell"><table><thead><tr><th>Service function</th><th>Scenario</th><th>Problem type</th><th>Customer symptom</th><th>Trigger condition</th><th>Tickets</th><th>Ticket-recorded solution</th><th>Evidence confidence</th><th>Coverage status</th></tr></thead><tbody id="uncovered-rows"></tbody></table></section>
</main>
<script>
const overlay = __OVERLAY_DATA__;
const selector = document.getElementById("tree-selector");
const sourceImage = document.getElementById("source-tree-image");
const sourceLink = document.getElementById("source-link");
function cell(row, value, className = "") { const item = document.createElement("td"); item.textContent = String(value || ""); if (className) item.className = className; row.appendChild(item); }
function fillRows(bodyId, rows, values) { const body = document.getElementById(bodyId); body.replaceChildren(); rows.forEach((item) => { const row = document.createElement("tr"); values.forEach(([key, className]) => cell(row, item[key], className)); body.appendChild(row); }); }
function selectTree(treeId) {
  const tree = overlay.reference_trees.find((item) => item.tree_id === treeId) || overlay.reference_trees[0];
  if (!tree) return;
  sourceImage.src = tree.source_asset || "";
  sourceImage.alt = `${tree.tree_name_en || tree.tree_id} original after-sales decision tree`;
  sourceLink.href = tree.source_asset || "";
  sourceLink.textContent = tree.source_image || "Open original";
  document.getElementById("reference-tree-id").textContent = tree.reference_tree_id || tree.tree_id;
  document.getElementById("scenario-count").textContent = String(tree.phase09_scenario_count || 0);
  document.getElementById("ticket-count").textContent = String(tree.phase09_ticket_count || 0);
  document.getElementById("graph-status").textContent = tree.source_graph_status || "";
  document.getElementById("mapping-status").textContent = tree.branch_mapping_status || "";
  document.getElementById("draft-status").textContent = tree.legacy_path_draft_status || "";
  const support = overlay.phase09_data_support.filter((item) => item.tree_id === tree.tree_id);
  fillRows("support-rows", support, [["problem_type", ""], ["scenario_count", "count"], ["ticket_count", "count"], ["recorded_solution_ticket_count", "count"], ["sample_customer_symptoms", ""], ["sample_trigger_conditions", ""], ["sample_recorded_solutions", ""], ["mapping_status", ""]]);
  const candidates = overlay.unmapped_scenario_candidates.filter((item) => item.tree_id === tree.tree_id).slice(0, 50);
  fillRows("candidate-rows", candidates, [["scenario_id", ""], ["problem_type", ""], ["customer_symptom", ""], ["trigger_condition", ""], ["ticket_count", "count"], ["recorded_solution", ""], ["evidence_confidence", ""], ["reference_branch_id", ""], ["mapping_status", ""]]);
}
overlay.reference_trees.forEach((tree) => { const option = document.createElement("option"); option.value = tree.tree_id; option.textContent = `${tree.tree_name_cn || tree.tree_id} (${tree.phase09_ticket_count || 0} tickets)`; selector.appendChild(option); });
selector.addEventListener("change", (event) => selectTree(event.target.value));
if (overlay.reference_trees.length) { selector.value = overlay.reference_trees[0].tree_id; selectTree(selector.value); }
fillRows("uncovered-rows", overlay.uncovered_service_function_candidates.slice(0, 50), [["service_function_id", ""], ["scenario_id", ""], ["problem_type", ""], ["customer_symptom", ""], ["trigger_condition", ""], ["ticket_count", "count"], ["recorded_solution", ""], ["evidence_confidence", ""], ["coverage_status", ""]]);
</script>
</body>
</html>
"""
    return template.replace("__OVERLAY_DATA__", html_data)


def write_reference_tree_augmentation_artifacts(
    *,
    overlay: dict[str, Any],
    output_xlsx: Path,
    output_json: Path,
    output_html: Path,
) -> None:
    """Write the reference-tree overlay as a workbook, JSON payload, and browser view."""
    output_xlsx.parent.mkdir(parents=True, exist_ok=True)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_html.parent.mkdir(parents=True, exist_ok=True)

    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)

    overview = workbook.create_sheet("00_Overview")
    overview_rows = [
        ["Phase 09 After-Sales Reference Tree Evidence Overlay"],
        [],
        ["Source tree policy", overlay["data_policy"]["source_tree_policy"]],
        ["Mapping policy", overlay["data_policy"]["mapping_policy"]],
        ["Reference source trees", len(overlay["reference_trees"])],
        ["Phase 09 data-support groups", len(overlay["phase09_data_support"])],
        ["Unmapped scenario candidates", len(overlay["unmapped_scenario_candidates"])],
        ["Uncovered service-function candidates", len(overlay["uncovered_service_function_candidates"])],
        ["Graph structuring review items", len(overlay["graph_structuring_review_queue"])],
    ]
    for row in overview_rows:
        overview.append(row)
    overview.cell(row=1, column=1).font = Font(bold=True, size=14)
    overview.column_dimensions["A"].width = 34
    overview.column_dimensions["B"].width = 110
    for row in overview.iter_rows():
        for cell in row:
            cell.alignment = _WRAP

    catalog_headers = [
        "Tree ID", "Reference Tree ID", "Tree Name CN", "Tree Name EN",
        "Source Image", "Source Asset", "Source Tree Authority", "Source Graph Status",
        "Legacy Draft Status", "Legacy Draft Issues", "Service Function ID", "Tree Version",
        "Phase 09 Scenario Count", "Phase 09 Ticket Count", "Branch Mapping Status",
    ]
    catalog_rows = [{
        "Tree ID": tree["tree_id"],
        "Reference Tree ID": tree["reference_tree_id"],
        "Tree Name CN": tree["tree_name_cn"],
        "Tree Name EN": tree["tree_name_en"],
        "Source Image": tree["source_image"],
        "Source Asset": tree["source_asset"],
        "Source Tree Authority": tree["source_tree_authority"],
        "Source Graph Status": tree["source_graph_status"],
        "Legacy Draft Status": tree["legacy_path_draft_status"],
        "Legacy Draft Issues": "; ".join(tree["legacy_path_draft_issues"]),
        "Service Function ID": tree["service_function_id"],
        "Tree Version": tree["tree_version"],
        "Phase 09 Scenario Count": tree["phase09_scenario_count"],
        "Phase 09 Ticket Count": tree["phase09_ticket_count"],
        "Branch Mapping Status": tree["branch_mapping_status"],
    } for tree in overlay["reference_trees"]]
    _append_table(
        workbook.create_sheet("01_Reference_Tree_Catalog"), catalog_headers, catalog_rows
    )

    support_headers = [
        "Tree ID", "Reference Tree ID", "Service Function ID", "Problem Type",
        "Scenario Count", "Ticket Count", "High Confidence Scenarios",
        "Medium Confidence Scenarios", "Low Confidence Scenarios",
        "Recorded Solution Scenarios", "Recorded Solution Tickets",
        "Sample Customer Symptoms", "Sample Trigger Conditions",
        "Sample Recorded Solutions", "Mapping Status",
    ]
    support_rows = [{
        "Tree ID": support["tree_id"],
        "Reference Tree ID": support["reference_tree_id"],
        "Service Function ID": support["service_function_id"],
        "Problem Type": support["problem_type"],
        "Scenario Count": support["scenario_count"],
        "Ticket Count": support["ticket_count"],
        "High Confidence Scenarios": support["high_confidence_scenario_count"],
        "Medium Confidence Scenarios": support["medium_confidence_scenario_count"],
        "Low Confidence Scenarios": support["low_confidence_scenario_count"],
        "Recorded Solution Scenarios": support["recorded_solution_scenario_count"],
        "Recorded Solution Tickets": support["recorded_solution_ticket_count"],
        "Sample Customer Symptoms": support["sample_customer_symptoms"],
        "Sample Trigger Conditions": support["sample_trigger_conditions"],
        "Sample Recorded Solutions": support["sample_recorded_solutions"],
        "Mapping Status": support["mapping_status"],
    } for support in overlay["phase09_data_support"]]
    _append_table(
        workbook.create_sheet("02_Phase09_Data_Support"), support_headers, support_rows
    )

    candidate_headers = [
        "Tree ID", "Reference Tree ID", "Service Function ID", "Reference Branch ID",
        "Mapping Scope", "Mapping Status", "Scenario ID", "Problem Type",
        "Customer Symptom", "Trigger Condition", "Ticket Count",
        "Ticket-recorded Solution", "Evidence Confidence", "Representative Ticket ID",
    ]
    candidate_rows = [{
        "Tree ID": candidate["tree_id"],
        "Reference Tree ID": candidate["reference_tree_id"],
        "Service Function ID": candidate["service_function_id"],
        "Reference Branch ID": candidate["reference_branch_id"],
        "Mapping Scope": candidate["mapping_scope"],
        "Mapping Status": candidate["mapping_status"],
        "Scenario ID": candidate["scenario_id"],
        "Problem Type": candidate["problem_type"],
        "Customer Symptom": candidate["customer_symptom"],
        "Trigger Condition": candidate["trigger_condition"],
        "Ticket Count": candidate["ticket_count"],
        "Ticket-recorded Solution": candidate["recorded_solution"],
        "Evidence Confidence": candidate["evidence_confidence"],
        "Representative Ticket ID": candidate["rep_ticket_id"],
    } for candidate in overlay["unmapped_scenario_candidates"]]
    _append_table(
        workbook.create_sheet("03_Unmapped_Scenario_Review"),
        candidate_headers,
        candidate_rows,
    )

    uncovered_headers = [
        "Service Function ID", "Scenario ID", "Problem Type", "Customer Symptom",
        "Trigger Condition", "Ticket Count", "Ticket-recorded Solution",
        "Evidence Confidence", "Representative Ticket ID", "Coverage Status",
    ]
    uncovered_rows = [{
        "Service Function ID": candidate["service_function_id"],
        "Scenario ID": candidate["scenario_id"],
        "Problem Type": candidate["problem_type"],
        "Customer Symptom": candidate["customer_symptom"],
        "Trigger Condition": candidate["trigger_condition"],
        "Ticket Count": candidate["ticket_count"],
        "Ticket-recorded Solution": candidate["recorded_solution"],
        "Evidence Confidence": candidate["evidence_confidence"],
        "Representative Ticket ID": candidate["rep_ticket_id"],
        "Coverage Status": candidate["coverage_status"],
    } for candidate in overlay["uncovered_service_function_candidates"]]
    _append_table(
        workbook.create_sheet("04_Uncovered_Service_Gaps"),
        uncovered_headers,
        uncovered_rows,
    )

    review_headers = [
        "Tree ID", "Reference Tree ID", "Source Image", "Source Asset",
        "Service Function ID", "Review Status", "Legacy Draft Status",
        "Legacy Draft Issues", "Phase 09 Scenario Count", "Phase 09 Ticket Count",
        "Required Structure",
    ]
    review_rows = [{
        "Tree ID": item["tree_id"],
        "Reference Tree ID": item["reference_tree_id"],
        "Source Image": item["source_image"],
        "Source Asset": item["source_asset"],
        "Service Function ID": item["service_function_id"],
        "Review Status": item["review_status"],
        "Legacy Draft Status": item["legacy_path_draft_status"],
        "Legacy Draft Issues": item["legacy_path_draft_issues"],
        "Phase 09 Scenario Count": item["phase09_scenario_count"],
        "Phase 09 Ticket Count": item["phase09_ticket_count"],
        "Required Structure": item["required_structure"],
    } for item in overlay["graph_structuring_review_queue"]]
    _append_table(
        workbook.create_sheet("05_Graph_Structuring_Review"), review_headers, review_rows
    )

    dictionary_headers = ["Field", "Layer", "Definition", "Rule"]
    dictionary_rows = [
        {
            "Field": "Original after-sales source tree",
            "Layer": "Authority",
            "Definition": "The supplied source image is the authoritative diagnostic tree.",
            "Rule": "Phase 09 data cannot alter it automatically.",
        },
        {
            "Field": "Human-verified source graph",
            "Layer": "Structure",
            "Definition": "Node IDs, directed edges, branch outcomes, and source-image bounds captured from the source image.",
            "Rule": "Required before any scenario can receive a Reference Branch ID.",
        },
        {
            "Field": "Phase 09 data support",
            "Layer": "Evidence overlay",
            "Definition": "Historical problem observations aggregated by source tree and service function.",
            "Rule": "Service-function association only; it is not a branch match.",
        },
        {
            "Field": "Unmapped scenario candidate",
            "Layer": "Review queue",
            "Definition": "A Phase 09 scenario associated with a reference tree's service function.",
            "Rule": "Always remains unmapped_pending_branch_verification until a reviewer selects an approved source branch.",
        },
        {
            "Field": "Uncovered service-function candidate",
            "Layer": "Coverage gap",
            "Definition": "A Phase 09 scenario whose service function has no matching after-sales source tree.",
            "Rule": "Do not force-map it to a neighboring tree; review whether to add a branch, add a tree, or approve an explicit mapping.",
        },
        {
            "Field": "Ticket-recorded solution",
            "Layer": "Evidence overlay",
            "Definition": "Recovery text recorded in the historical Ticket.",
            "Rule": "May be a workaround; it is not a source-tree modification, root cause, or approved repair instruction.",
        },
        {
            "Field": "Graph structuring review",
            "Layer": "Governance",
            "Definition": "Work required to preserve original node/edge semantics in a structured graph.",
            "Rule": "Review source image fidelity before enabling branch-level data support.",
        },
    ]
    _append_table(
        workbook.create_sheet("06_Data_Dictionary"), dictionary_headers, dictionary_rows
    )

    workbook.save(output_xlsx)
    output_json.write_text(
        json.dumps(overlay, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    output_html.write_text(
        render_reference_tree_augmentation_html(overlay), encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a Phase 09 evidence overlay for after-sales reference trees."
    )
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH)
    parser.add_argument(
        "--scenario-workbook", type=Path, default=DEFAULT_V12_WORKBOOK_PATH
    )
    parser.add_argument(
        "--knowledge-base", type=Path, default=DEFAULT_V12_KNOWLEDGE_BASE_PATH
    )
    parser.add_argument(
        "--legacy-drafts", type=Path, default=DEFAULT_LEGACY_DRAFT_DIRECTORY
    )
    parser.add_argument("--output-xlsx", type=Path, default=DEFAULT_OUTPUT_XLSX)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--output-html", type=Path, default=DEFAULT_OUTPUT_HTML)
    args = parser.parse_args(argv)

    overlay = build_reference_tree_augmentation_from_files(
        registry_path=args.registry,
        scenario_workbook_path=args.scenario_workbook,
        knowledge_base_path=args.knowledge_base,
        legacy_draft_directory=args.legacy_drafts,
    )
    write_reference_tree_augmentation_artifacts(
        overlay=overlay,
        output_xlsx=args.output_xlsx,
        output_json=args.output_json,
        output_html=args.output_html,
    )
    print(f"Saved workbook: {args.output_xlsx}")
    print(f"Saved JSON: {args.output_json}")
    print(f"Saved HTML: {args.output_html}")
    print(f"Reference trees: {len(overlay['reference_trees'])}")
    print(f"Unmapped scenario candidates: {len(overlay['unmapped_scenario_candidates'])}")
    print(
        "Uncovered service-function candidates: "
        f"{len(overlay['uncovered_service_function_candidates'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())