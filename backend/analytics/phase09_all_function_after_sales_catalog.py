#!/usr/bin/env python3
"""Build a traceable all-function after-sales decision-tree HTML catalog.

The catalog keeps two independent layers visible for every service function:
the V12 evidence-derived tree and, where available, an AI-structured source
tree rebuilt from the supplied after-sales path draft. The source image remains
available beside the reconstructed topology.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
import re
from typing import Any

from openpyxl import load_workbook


REPO_ROOT = Path(__file__).resolve().parents[2]
PHASE09_EXPORT_DIR = REPO_ROOT / "exports" / "idcevo_phase09"
SOURCE_TREE_DIR = PHASE09_EXPORT_DIR / "decision_tree_sources"
DEFAULT_SCENARIO_WORKBOOK = (
    REPO_ROOT
    / "exports"
    / "idcevo_phase09"
    / "IDCEVO_Phase09_Field_Escape_Scenario_Library_V12_Symptom_to_Solution_Trees.xlsx"
)
DEFAULT_KNOWLEDGE_BASE = (
    REPO_ROOT
    / "exports"
    / "idcevo_phase09"
    / "IDCEVO_Phase09_Field_Escape_Scenario_Library_V12_Symptom_to_Solution_Trees_Decision_Tree_Knowledge_Base.json"
)
DEFAULT_REGISTRY = SOURCE_TREE_DIR / "tree_registry.json"
DEFAULT_MANIFEST = SOURCE_TREE_DIR / "lossless_webp" / "manifest.json"
DEFAULT_EXTRACTED_DIRECTORY = SOURCE_TREE_DIR / "extracted"
DEFAULT_OUTPUT_HTML = (
    PHASE09_EXPORT_DIR
    / "IDCEVO_Phase09_All_Function_AfterSales_Decision_Tree_Catalog_V1.html"
)
DEFAULT_OUTPUT_JSON = (
    PHASE09_EXPORT_DIR
    / "IDCEVO_Phase09_All_Function_AfterSales_Decision_Tree_Catalog_V1.json"
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _integer(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _source_key(value: Any) -> str:
    normalized = _text(value).replace("\\", "/").rsplit("/", 1)[-1]
    return Path(normalized).stem.casefold()


def _load_json_object(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8-sig") as source:
        payload = json.load(source)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return payload


def _node_id(tree_id: str, node_path: str, path_depth: int) -> str:
    if path_depth == 0:
        return "ROOT"
    digest = hashlib.sha256(node_path.encode("utf-8")).hexdigest()[:10].upper()
    return f"SRC-{tree_id}-{digest}"


def _path_segments(node_path: Any) -> list[str]:
    return [
        segment.strip()
        for segment in _text(node_path).split(" > ")
        if segment.strip()
    ]


def build_ai_reviewed_source_graph(draft: dict[str, Any]) -> dict[str, Any]:
    """Rebuild directed parent-child topology from a path-only source draft.

    The function validates the source draft's hierarchy, normalizes its
    one-based level convention, and records only `NEXT` edges because the path
    draft does not preserve visible TRUE/FALSE labels or image coordinates.
    """
    tree_id = _text(draft.get("tree_id"))
    if not tree_id:
        raise ValueError("Source draft is missing tree_id")
    raw_nodes = draft.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise ValueError(f"Source draft {tree_id} has no nodes")

    by_path: dict[str, dict[str, Any]] = {}
    review_errors: list[str] = []
    review_warnings: list[str] = []
    for raw_node in raw_nodes:
        if not isinstance(raw_node, dict):
            review_errors.append("invalid_node_record")
            continue
        segments = _path_segments(raw_node.get("node_path"))
        if not segments:
            review_errors.append("node_path_missing")
            continue
        node_path = " > ".join(segments)
        if node_path in by_path:
            review_errors.append(f"duplicate_node_path:{node_path}")
            continue
        source_level = _integer(raw_node.get("level"))
        expected_source_level = len(segments)
        if source_level != expected_source_level:
            review_errors.append(
                f"level_path_mismatch:{node_path}:{source_level}!={expected_source_level}"
            )
        by_path[node_path] = {
            "raw": raw_node,
            "segments": segments,
            "node_path": node_path,
            "path_depth": len(segments) - 1,
            "source_level": source_level,
        }

    roots = [item for item in by_path.values() if item["path_depth"] == 0]
    if len(roots) != 1:
        review_errors.append(f"root_count:{len(roots)}")
    for item in by_path.values():
        if item["path_depth"] == 0:
            continue
        parent_path = " > ".join(item["segments"][:-1])
        if parent_path not in by_path:
            review_errors.append(f"parent_path_missing:{item['node_path']}")

    if review_errors:
        details = "; ".join(sorted(set(review_errors)))
        raise ValueError(f"Cannot structurally review {tree_id}: {details}")

    children_by_path: dict[str, list[str]] = defaultdict(list)
    for item in by_path.values():
        if item["path_depth"]:
            children_by_path[" > ".join(item["segments"][:-1])].append(
                item["node_path"]
            )

    nodes = []
    path_to_node_id: dict[str, str] = {}
    for item in sorted(by_path.values(), key=lambda value: (value["path_depth"], value["node_path"])):
        raw_node = item["raw"]
        node_path = item["node_path"]
        path_depth = item["path_depth"]
        is_terminal = not children_by_path.get(node_path)
        source_leaf = bool(raw_node.get("is_leaf"))
        if source_leaf != is_terminal:
            review_warnings.append(f"leaf_path_mismatch:{node_path}")
        node_id = _node_id(tree_id, node_path, path_depth)
        path_to_node_id[node_path] = node_id
        nodes.append({
            "node_id": node_id,
            "parent_node_id": "",
            "node_type": (
                "start"
                if path_depth == 0
                else "resolution"
                if is_terminal and _text(raw_node.get("solution_cn"))
                else "decision"
            ),
            "source_text": item["segments"][-1],
            "source_path": node_path,
            "path_depth": path_depth,
            "source_level": item["source_level"],
            "symptom": _text(raw_node.get("symptom_cn")),
            "possible_cause": _text(raw_node.get("possible_cause_cn")),
            "check_step": _text(raw_node.get("check_step_cn")),
            "solution": _text(raw_node.get("solution_cn")),
            "escalation_path": _text(raw_node.get("escalation_path")),
            "dtc_keywords": _text(raw_node.get("dtc_keywords")),
            "defect_mode": _text(raw_node.get("defect_mode")),
            "root_cause_category": _text(raw_node.get("root_cause_category")),
            "source_confidence": _text(raw_node.get("confidence")),
            "source_leaf": source_leaf,
            "is_terminal": is_terminal,
            "node_provenance": "legacy_path_draft",
            "review_status": "ai_structured_reviewed",
        })

    node_by_id = {node["node_id"]: node for node in nodes}
    edges = []
    for item in sorted(by_path.values(), key=lambda value: value["node_path"]):
        if item["path_depth"] == 0:
            continue
        parent_path = " > ".join(item["segments"][:-1])
        node_id = path_to_node_id[item["node_path"]]
        parent_node_id = path_to_node_id[parent_path]
        node_by_id[node_id]["parent_node_id"] = parent_node_id
        edges.append({
            "edge_id": f"E-{parent_node_id}-{node_id}",
            "from_node_id": parent_node_id,
            "to_node_id": node_id,
            "branch_outcome": "NEXT",
            "edge_provenance": "path_hierarchy_inference",
            "review_status": "ai_structured_reviewed",
        })

    return {
        "schema_version": "phase09-ai-reviewed-source-graph-v1",
        "tree_id": tree_id,
        "tree_name_cn": _text(draft.get("tree_name_cn")),
        "tree_name_en": _text(draft.get("tree_name_en")),
        "source_image": _text(draft.get("source_image")),
        "graph_status": "ai_structured_reviewed",
        "structural_validation_status": "pass",
        "review_method": "path_hierarchy_reconstruction",
        "review_limitations": (
            "Parent-child topology is reconstructed from the extracted node_path "
            "hierarchy. The draft does not preserve source-image coordinates or "
            "explicit TRUE/FALSE branch labels."
        ),
        "source_path_node_count": len(nodes),
        "source_path_edge_count": len(edges),
        "review_warnings": sorted(set(review_warnings)),
        "nodes": nodes,
        "edges": edges,
    }


def _read_worksheet_rows(
    workbook_path: Path, sheet_name: str
) -> list[dict[str, Any]]:
    workbook = load_workbook(workbook_path, read_only=True, data_only=True)
    try:
        if sheet_name not in workbook.sheetnames:
            raise ValueError(f"Missing {sheet_name} in {workbook_path}")
        worksheet = workbook[sheet_name]
        rows = worksheet.iter_rows(values_only=True)
        try:
            headers = [_text(value) for value in next(rows)]
        except StopIteration as error:
            raise ValueError(f"Missing headers in {sheet_name} of {workbook_path}") from error
        return [
            {
                header: row[index] if index < len(row) else ""
                for index, header in enumerate(headers)
                if header
            }
            for row in rows
            if any(value is not None for value in row)
        ]
    finally:
        workbook.close()


def _first_existing_sheet_name(
    workbook_path: Path, candidates: tuple[str, ...]
) -> str:
    workbook = load_workbook(workbook_path, read_only=True, data_only=True)
    try:
        for sheet_name in candidates:
            if sheet_name in workbook.sheetnames:
                return sheet_name
    finally:
        workbook.close()
    raise ValueError(
        f"Missing expected worksheet in {workbook_path}: {', '.join(candidates)}"
    )


def _load_source_graphs(
    *,
    knowledge_base: dict[str, Any],
    registry_path: Path,
    manifest_path: Path,
    extracted_directory: Path,
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    registry = _load_json_object(registry_path)
    manifest = _load_json_object(manifest_path)
    registry_by_source = {
        _source_key(tree.get("source_image")): tree
        for tree in registry.get("trees", [])
        if isinstance(tree, dict) and _source_key(tree.get("source_image"))
    }
    manifest_by_source = {
        _source_key(image.get("source_filename")): image
        for image in manifest.get("images", [])
        if isinstance(image, dict) and _source_key(image.get("source_filename"))
    }
    graphs_by_service: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unresolved_references = []
    for reference in knowledge_base.get("after_sales_reference_trees", []):
        if not isinstance(reference, dict):
            continue
        source_file = _text(reference.get("source_file"))
        source_key = _source_key(source_file)
        registry_tree = registry_by_source.get(source_key)
        if not registry_tree:
            unresolved_references.append(
                f"registry_missing:{_text(reference.get('reference_tree_id'))}"
            )
            continue
        tree_id = _text(registry_tree.get("tree_id"))
        draft_path = extracted_directory / f"{tree_id}.json"
        if not draft_path.is_file():
            unresolved_references.append(
                f"draft_missing:{_text(reference.get('reference_tree_id'))}"
            )
            continue
        draft = _load_json_object(draft_path)
        graph = build_ai_reviewed_source_graph(draft)
        image_metadata = manifest_by_source.get(source_key, {})
        graph.update({
            "reference_tree_id": _text(reference.get("reference_tree_id")),
            "tree_version": _text(reference.get("tree_version")),
            "service_function_id": _text(reference.get("service_function_id")),
            "source_file": source_file,
            "source_asset": _text(reference.get("source_asset")),
            "source_sha256": _text(image_metadata.get("output_sha256")),
            "source_width": _integer(image_metadata.get("width")),
            "source_height": _integer(image_metadata.get("height")),
        })
        graphs_by_service[graph["service_function_id"]].append(graph)
    for service_function_id in graphs_by_service:
        graphs_by_service[service_function_id].sort(
            key=lambda graph: (graph["tree_id"], graph["source_file"])
        )
    return dict(graphs_by_service), sorted(set(unresolved_references))


_PATTERN_NODE_TYPES = {"Symptom Pattern", "Trigger Pattern"}
_PATTERN_PREFIX_BY_NODE_TYPE = {
    "Symptom Pattern": "Observed symptom patterns:",
    "Trigger Pattern": "Observed trigger patterns:",
}
_GENERIC_PATTERN_SEGMENTS = {
    "other observed symptom pattern",
    "unspecified symptom pattern",
    "other reported trigger",
    "no trigger recorded",
    "no recorded value",
}
_PATTERN_COUNT_RE = re.compile(
    r"^(?P<label>.*?)(?:\s*\((?P<count>\d+)\s+(?:tickets?|groups?)\))?$",
    re.IGNORECASE,
)


def _visual_pattern_phrase(value: str) -> str:
    """Make a compact evidence phrase without repeated UI/template wording."""
    compact = " ".join(value.split()).strip()
    if "|" in compact:
        pieces = [piece.strip() for piece in compact.split("|") if piece.strip()]
        compact = " + ".join(pieces[:3])
        if len(pieces) > 3:
            compact += " + ..."
    if compact and compact[0].islower():
        compact = compact[0].upper() + compact[1:]
    return compact


def compact_v1_pattern_label(node_type: str, question_or_solution: str) -> str:
    """Keep specific ranked pattern evidence in a constrained SVG node."""
    raw_label = _text(question_or_solution)
    if node_type not in _PATTERN_NODE_TYPES or not raw_label:
        return raw_label
    prefix = _PATTERN_PREFIX_BY_NODE_TYPE[node_type]
    summary = raw_label.removeprefix(prefix).strip()
    specific_segments = []
    fallback_segments = []
    for raw_segment in summary.split(";"):
        segment = " ".join(raw_segment.split()).strip()
        if not segment:
            continue
        match = _PATTERN_COUNT_RE.match(segment)
        if not match:
            continue
        phrase = _visual_pattern_phrase(match.group("label") or "")
        count = match.group("count")
        normalized_phrase = phrase.casefold()
        if normalized_phrase.startswith("other observed variants"):
            continue
        rendered = f"{phrase} ({count})" if count else phrase
        if normalized_phrase in _GENERIC_PATTERN_SEGMENTS:
            fallback_segments.append(rendered)
        else:
            specific_segments.append(rendered)
    selected_segments = specific_segments[:3] or fallback_segments[:2]
    return " · ".join(selected_segments) or raw_label


def _v12_data_nodes(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nodes = []
    for record in records:
        node_id = _text(record.get("Node ID"))
        if not node_id:
            continue
        nodes.append({
            "node_id": node_id,
            "parent_node_id": _text(record.get("Parent Node ID")),
            "node_type": _text(record.get("Node Type")),
            "data_key": _text(record.get("Data Key")),
            "question_or_solution": _text(record.get("Question / Solution")),
            "visual_label": compact_v1_pattern_label(
                _text(record.get("Node Type")),
                _text(record.get("Question / Solution")),
            ),
            "evidence_source": _text(record.get("Evidence Source")),
            "source_scenario_count": _integer(record.get("Source Scenario Count")),
            "source_ticket_count": _integer(record.get("Source Ticket Count")),
            "source_scenario_ids": _text(record.get("Source Scenario IDs")),
            "solution_status": _text(record.get("Solution Status")),
            "node_source": _text(record.get("Node Source")),
        })
    return nodes


def _fallback_data_node(
    *, service_function_id: str, service_function: str, scenario_count: int, ticket_count: int
) -> dict[str, Any]:
    return {
        "node_id": "START",
        "parent_node_id": "",
        "node_type": "Entry",
        "data_key": service_function_id,
        "question_or_solution": service_function,
        "visual_label": service_function,
        "evidence_source": "",
        "source_scenario_count": scenario_count,
        "source_ticket_count": ticket_count,
        "source_scenario_ids": "",
        "solution_status": "",
        "node_source": "catalog_scaffold_no_field_data",
    }


def _v12_scenario_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "scenario_id": _text(record.get("Scenario ID")),
            "problem_type": _text(record.get("Problem Type")),
            "customer_symptom": _text(record.get("Customer Symptom")),
            "trigger_condition": _text(record.get("Trigger Condition")),
            "ticket_count": _integer(record.get("Ticket Count")),
            "risk_band": _text(record.get("Risk Band")),
            "risk_index": record.get("Risk Index") or "",
            "representative_ticket_id": _text(record.get("Rep Ticket ID")),
            "representative_title": _text(record.get("Rep Title")),
            "recorded_recovery_action": _text(record.get("Recorded Recovery Action")),
            "evidence_confidence": _text(record.get("Evidence Confidence")),
        }
        for record in records
        if _text(record.get("Scenario ID"))
    ]


def build_all_function_catalog_from_files(
    *,
    scenario_workbook_path: Path,
    knowledge_base_path: Path,
    registry_path: Path,
    manifest_path: Path,
    extracted_directory: Path,
) -> dict[str, Any]:
    """Build a function-complete catalog from V12 evidence and source drafts."""
    knowledge_base = _load_json_object(knowledge_base_path)
    tree_definitions = [
        tree
        for tree in knowledge_base.get("decision_trees", [])
        if isinstance(tree, dict)
        and _text(tree.get("service_function_id"))
        and _text(tree.get("service_function"))
    ]
    if not tree_definitions:
        raise ValueError(f"No decision trees found in {knowledge_base_path}")

    catalog_records = _read_worksheet_rows(
        scenario_workbook_path, "03_Service_Function_Catalog"
    )
    node_sheet_name = _first_existing_sheet_name(
        scenario_workbook_path,
        ("05_Decision_Tree_Nodes", "04_Decision_Tree_Nodes"),
    )
    node_records = _read_worksheet_rows(
        scenario_workbook_path, node_sheet_name
    )
    scenario_records = _read_worksheet_rows(
        scenario_workbook_path, "01_Scenario_Library"
    )
    catalog_by_service = {
        _text(record.get("Service Function")): record
        for record in catalog_records
        if _text(record.get("Service Function"))
    }
    nodes_by_service: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in node_records:
        service_function = _text(record.get("Service Function"))
        if service_function:
            nodes_by_service[service_function].append(record)
    scenarios_by_service: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in scenario_records:
        service_function = _text(record.get("Service Function"))
        if service_function:
            scenarios_by_service[service_function].append(record)

    source_graphs_by_service, unresolved_references = _load_source_graphs(
        knowledge_base=knowledge_base,
        registry_path=registry_path,
        manifest_path=manifest_path,
        extracted_directory=extracted_directory,
    )
    functions = []
    for tree in tree_definitions:
        service_function_id = _text(tree.get("service_function_id"))
        service_function = _text(tree.get("service_function"))
        catalog_record = catalog_by_service.get(service_function, {})
        scenario_count = _integer(
            catalog_record.get("Scenario Count", tree.get("scenario_count", 0))
        )
        ticket_count = _integer(
            catalog_record.get("Ticket Count", tree.get("ticket_count", 0))
        )
        data_tree_nodes = _v12_data_nodes(nodes_by_service.get(service_function, []))
        if not data_tree_nodes:
            data_tree_nodes = [_fallback_data_node(
                service_function_id=service_function_id,
                service_function=service_function,
                scenario_count=scenario_count,
                ticket_count=ticket_count,
            )]
        functions.append({
            "tree_id": _text(tree.get("tree_id")) or f"DT-{service_function_id}",
            "service_function_id": service_function_id,
            "service_function": service_function,
            "service_function_label": _text(tree.get("service_function_label"))
            or service_function,
            "service_domain": _text(tree.get("service_domain")),
            "catalog_status": _text(tree.get("catalog_status")),
            "reference_tree_ids": list(tree.get("reference_tree_ids") or []),
            "scenario_count": scenario_count,
            "ticket_count": ticket_count,
            "source_ticket_mention_count": _integer(
                catalog_record.get(
                    "Source Ticket Mentions", tree.get("source_ticket_mention_count", 0)
                )
            ),
            "phase09_evidence_status": _text(
                catalog_record.get(
                    "Phase 09 Evidence Status", tree.get("phase09_evidence_status")
                )
            ),
            "problem_types": _text(catalog_record.get("Problem Types")),
            "diagnostic_layers": _text(catalog_record.get("Diagnostic Layers")),
            "solution_source_policy": _text(
                catalog_record.get("Solution Source Policy")
            ),
            "tree_generation": _text(catalog_record.get("Tree Generation")),
            "source_status": _text(catalog_record.get("Source Status")),
            "data_tree_nodes": data_tree_nodes,
            "scenario_records": _v12_scenario_records(
                scenarios_by_service.get(service_function, [])
            ),
            "source_graphs": source_graphs_by_service.get(service_function_id, []),
        })

    functions.sort(
        key=lambda function: (
            function["service_domain"].casefold(),
            function["service_function_label"].casefold(),
        )
    )
    source_graph_count = sum(len(function["source_graphs"]) for function in functions)
    return {
        "schema_version": "phase09-all-function-after-sales-catalog-v1",
        "data_policy": {
            "source_tree_policy": (
                "Original after-sales images remain visible and authoritative as source "
                "assets. AI review reconstructs only the path hierarchy preserved in the "
                "draft and does not invent branch outcomes."
            ),
            "field_evidence_policy": (
                "V12 nodes and ticket-recorded recovery text are historical evidence. "
                "They do not modify the source image or establish a root cause."
            ),
            "review_status_policy": (
                "ai_structured_reviewed means the extracted hierarchy passed topology "
                "validation; it is not a human certification of source-image transcription."
            ),
        },
        "summary": {
            "service_function_count": len(functions),
            "field_scenario_count": sum(function["scenario_count"] for function in functions),
            "field_ticket_count": sum(function["ticket_count"] for function in functions),
            "image_backed_function_count": sum(
                bool(function["source_graphs"]) for function in functions
            ),
            "ai_structured_source_graph_count": source_graph_count,
            "ai_structured_source_node_count": sum(
                len(graph["nodes"])
                for function in functions
                for graph in function["source_graphs"]
            ),
            "unresolved_reference_count": len(unresolved_references),
        },
        "unresolved_references": unresolved_references,
        "functions": functions,
    }


def render_all_function_catalog_html(catalog: dict[str, Any]) -> str:
    """Render the all-function catalog as a standalone local HTML application."""
    serialized_catalog = json.dumps(catalog, ensure_ascii=False).replace(
        "<", "\\u003c"
    ).replace(">", "\\u003e").replace("&", "\\u0026")
    template = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>All-Function After-Sales Decision Tree Catalog</title>
<style>
:root { color: #17242c; background: #edf1ef; font-family: Aptos, "Noto Sans SC", "Microsoft YaHei", sans-serif; }
* { box-sizing: border-box; }
body { margin: 0; }
main { max-width: 1720px; margin: 0 auto; padding: 26px 28px 46px; }
header { border-bottom: 4px solid #1b786b; padding-bottom: 17px; }
h1 { margin: 0; color: #123c4a; font-size: 28px; letter-spacing: 0; }
p { line-height: 1.5; }
.subtitle { margin: 7px 0 0; color: #52616b; font-size: 15px; }
.summary { display: grid; grid-template-columns: repeat(6, minmax(112px, 1fr)); gap: 1px; margin-top: 20px; border: 1px solid #c7d0d6; background: #c7d0d6; }
.summary div { min-height: 66px; padding: 10px 12px; background: #fff; }
.summary span { display: block; color: #61717a; font-size: 12px; }
.summary strong { display: block; margin-top: 5px; color: #173c49; font-size: 17px; }
.controls { display: grid; grid-template-columns: minmax(220px, .7fr) minmax(280px, 1fr) minmax(280px, 1fr); gap: 14px; margin: 20px 0 14px; }
label { color: #33414b; font-size: 13px; font-weight: 700; }
input, select { display: block; width: 100%; min-height: 42px; margin-top: 6px; padding: 8px 11px; border: 1px solid #82949c; border-radius: 4px; background: #fff; color: #17242c; font: inherit; }
.ticket-filters { display: grid; grid-template-columns: minmax(160px, 220px) minmax(160px, 220px) auto; gap: 12px 16px; align-items: end; margin: 0 0 16px; padding: 14px 0; border-top: 1px solid #b9c6cc; border-bottom: 1px solid #b9c6cc; }
.ticket-filters[hidden] { display: none; }
.phase-filter { grid-column: 1 / -1; min-width: 0; margin: 0; padding: 0; border: 0; }
.phase-filter legend { margin-bottom: 8px; color: #33414b; font-size: 13px; font-weight: 700; }
.phase-options, .phase-actions, .filter-actions { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; }
.phase-options label { display: inline-flex; align-items: center; gap: 6px; min-height: 28px; font-weight: 400; cursor: pointer; }
.phase-options input { display: inline-block; flex: 0 0 auto; width: 16px; height: 16px; min-height: 0; margin: 0; padding: 0; accent-color: #1b786b; }
.phase-actions { margin-top: 6px; }
.filter-command { min-height: 30px; padding: 2px 0; border: 0; background: transparent; color: #0e6f70; font: inherit; font-size: 13px; text-decoration: underline; text-underline-offset: 3px; cursor: pointer; }
.ticket-filters > label { min-width: 0; }
.ticket-filters input[type="date"] { min-width: 0; font-size: 14px; }
.filter-status { grid-column: 1 / -1; color: #52616b; font-size: 13px; font-variant-numeric: tabular-nums; }
.tree-meta { display: grid; grid-template-columns: repeat(6, minmax(120px, 1fr)); gap: 1px; border: 1px solid #c7d0d6; background: #c7d0d6; }
.tree-meta div { min-height: 69px; padding: 10px 12px; background: #fff; }
.tree-meta span { display: block; color: #61717a; font-size: 12px; }
.tree-meta strong { display: block; margin-top: 5px; color: #173c49; font-size: 14px; line-height: 1.25; }
.review-note { margin: 14px 0; padding: 11px 13px; border-left: 4px solid #cb8528; background: #fff4df; color: #5b3a0c; font-size: 13px; }
.tabs { display: flex; gap: 1px; margin-top: 18px; border-bottom: 2px solid #1b786b; }
.tab { min-height: 42px; padding: 8px 15px; border: 1px solid #9aacb2; border-bottom: 0; background: #e8eeee; color: #263d47; font: inherit; font-weight: 700; cursor: pointer; }
.tab[aria-selected="true"] { border-color: #1b786b; background: #1b786b; color: #fff; }
.workspace { min-width: 0; display: grid; grid-template-columns: minmax(0, 1fr) minmax(300px, .36fr); gap: 16px; margin-top: 14px; align-items: start; }
.diagram-panel, .detail-panel, .table-shell { min-width: 0; border: 1px solid #b9c6cc; background: #fff; }
.panel-head { display: flex; justify-content: space-between; gap: 12px; align-items: center; padding: 10px 12px; border-bottom: 1px solid #c7d0d6; background: #eaf1f0; color: #173c49; font-size: 13px; font-weight: 700; }
.panel-head select { width: min(360px, 68%); min-height: 32px; margin: 0; padding: 4px 8px; }
.diagram-scroll { overflow: auto; min-height: 460px; max-height: 760px; }
svg { display: block; min-width: 940px; width: 100%; height: auto; }
.source-image { display: block; width: 100%; min-width: 940px; height: auto; }
.empty-state { padding: 28px; color: #536670; line-height: 1.5; }
.detail-panel { position: sticky; top: 12px; }
.detail-panel dl { margin: 0; }
.detail-panel div { padding: 10px 12px; border-bottom: 1px solid #d4dde0; }
.detail-panel div:last-child { border-bottom: 0; }
.detail-panel dt { color: #61717a; font-size: 12px; }
.detail-panel dd { margin: 4px 0 0; color: #1b3038; font-size: 13px; line-height: 1.45; white-space: pre-wrap; }
.detail-panel details { margin-top: 4px; }
.detail-panel summary { color: #0e6f70; cursor: pointer; font-size: 13px; font-weight: 700; }
.detail-raw { margin-top: 8px; color: #1b3038; font-size: 13px; line-height: 1.45; white-space: pre-wrap; }
.section-title { margin: 25px 0 9px; color: #123c4a; font-size: 18px; }
.table-shell { overflow: auto; max-height: 540px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 8px 9px; border: 1px solid #c7d0d6; text-align: left; vertical-align: top; line-height: 1.4; }
th { position: sticky; top: 0; z-index: 1; background: #123c4a; color: #fff; white-space: nowrap; }
td.count { text-align: right; white-space: nowrap; }
.link { color: #0e6f70; text-decoration-thickness: 1px; text-underline-offset: 2px; }
@media (max-width: 980px) { main { padding: 20px 16px 32px; } h1 { font-size: 23px; } .summary, .tree-meta { grid-template-columns: repeat(2, minmax(0, 1fr)); } .controls, .workspace { grid-template-columns: 1fr; } .detail-panel { position: static; } .diagram-scroll { min-height: 380px; } }
@media (max-width: 600px) { .ticket-filters { grid-template-columns: repeat(2, minmax(0, 1fr)); } .filter-actions { grid-column: 1 / -1; } .phase-options { gap: 5px 12px; } }
</style>
</head>
<body>
<main>
  <header>
    <h1>All-Function After-Sales Decision Tree Catalog</h1>
    <p class="subtitle">All V12 service functions, their evidence-derived decision trees, and AI-structured original after-sales reference trees where source images are available.</p>
  </header>
  <section class="summary" aria-label="Catalog summary">
    <div><span>Service functions</span><strong id="summary-functions"></strong></div>
    <div><span>Field scenarios</span><strong id="summary-scenarios"></strong></div>
    <div><span>Field tickets</span><strong id="summary-tickets"></strong></div>
    <div><span>Image-backed functions</span><strong id="summary-image-functions"></strong></div>
    <div><span>AI structured source graphs</span><strong id="summary-source-graphs"></strong></div>
    <div><span>AI structured source nodes</span><strong id="summary-source-nodes"></strong></div>
  </section>
  <section class="controls">
    <label for="function-filter">Find a function<input id="function-filter" type="search" placeholder="Function, domain, or ID"></label>
    <label for="function-selector">Service function<select id="function-selector"></select></label>
  </section>
    <form class="ticket-filters" id="ticket-filters" aria-label="Ticket filters" hidden>
        <fieldset class="phase-filter"><legend>Ticket phase</legend><div class="phase-options" id="ticket-phase-options"></div><div class="phase-actions"><button class="filter-command" type="button" data-phase-action="all">All phases</button><button class="filter-command" type="button" data-phase-action="none">Clear phases</button></div></fieldset>
        <label for="ticket-created-from">Created from (UTC)<input id="ticket-created-from" type="date"></label>
        <label for="ticket-created-through">Created through (UTC)<input id="ticket-created-through" type="date"></label>
        <div class="filter-actions"><button class="filter-command" id="reset-ticket-filters" type="button">Reset filters</button></div>
        <output class="filter-status" id="ticket-filter-status" aria-live="polite"></output>
    </form>
  <section class="tree-meta">
    <div><span>Function</span><strong id="meta-function"></strong></div>
    <div><span>Domain</span><strong id="meta-domain"></strong></div>
    <div><span>Field scenarios</span><strong id="meta-scenarios"></strong></div>
    <div><span>Field tickets</span><strong id="meta-tickets"></strong></div>
    <div><span>Source ticket mentions</span><strong id="meta-mentions"></strong></div>
    <div><span>Source-graph coverage</span><strong id="meta-source-graphs"></strong></div>
  </section>
  <p class="review-note" id="review-note"></p>
  <nav class="tabs" role="tablist" aria-label="Tree views">
    <button class="tab" type="button" role="tab" data-view="data" aria-selected="true">V12 Evidence Tree</button>
    <button class="tab" type="button" role="tab" data-view="source" aria-selected="false">AI Structured Source Tree</button>
    <button class="tab" type="button" role="tab" data-view="image" aria-selected="false">Original Source Image</button>
  </nav>
  <section class="workspace">
    <section class="diagram-panel">
      <header class="panel-head"><span id="diagram-title"></span><select id="source-selector" aria-label="Source tree reference"></select></header>
      <div class="diagram-scroll" id="diagram-scroll"></div>
    </section>
    <aside class="detail-panel"><header class="panel-head">Selected node</header><dl id="node-detail"></dl></aside>
  </section>
  <h2 class="section-title">Visible tree nodes</h2>
  <section class="table-shell"><table><thead><tr><th>Node</th><th>Parent</th><th>Type</th><th>Prompt / Source text</th><th>Evidence</th><th>Tickets</th><th>Status</th></tr></thead><tbody id="node-rows"></tbody></table></section>
  <h2 class="section-title">V12 historical scenarios</h2>
  <section class="table-shell"><table><thead><tr><th>Scenario</th><th>Problem type</th><th>Customer symptom</th><th>Trigger</th><th>Tickets</th><th>Recorded recovery</th><th>Confidence</th><th>Representative ticket</th></tr></thead><tbody id="scenario-rows"></tbody></table></section>
</main>
<script>
const catalog = __CATALOG_DATA__;
const NS = "http://www.w3.org/2000/svg";
const dataColors = { Entry: "#d7ead1", "Issue Group": "#d8e8f5", "Symptom Pattern": "#e8ddf3", "Trigger Pattern": "#fff0c8", "Ticket Solution": "#e2ece8" };
const sourceColors = { start: "#d7ead1", decision: "#d8e8f5", resolution: "#e2ece8", action: "#fff0c8", escalation: "#f7d7d2", note: "#eceff1" };
let activeFunction = null;
let activeView = "data";
let activeNode = null;
let activeSourceIndex = 0;
let visibleFunctions = catalog.functions;

function text(value) { return String(value ?? ""); }
function ticketPhaseCode(value) { const match = text(value).trim().match(/^(\d{2})(?:\D|$)/); return match ? match[1] : "Unknown"; }
function ticketCreationDay(record) { const timestamp = Date.parse(record.creation_time || ""); return Number.isFinite(timestamp) ? new Date(timestamp).toISOString().slice(0, 10) : ""; }
function filterDistribution(records, field) { const counts = new Map(); records.forEach((record) => { const value = text(record[field]); if (value) counts.set(value, (counts.get(value) || 0) + 1); }); return [...counts].sort(([left], [right]) => left.localeCompare(right)).map(([value, count]) => `${value}: ${count}`).join("; "); }
function filterValueSummary(values) {
    const counts = new Map(); values.filter(Boolean).forEach((value) => counts.set(value, (counts.get(value) || 0) + 1));
    const ranked = [...counts].sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]));
    const lines = ranked.slice(0, 3).map(([value, count]) => `${text(value).replace(/\s+/g, " ").slice(0, 420)} (${count} ticket(s))`);
    if (ranked.length > 3) lines.push(`${ranked.length - 3} additional distinct value(s)`);
    return lines.join("\n---\n");
}
function filteredNodeDetails(records, type) {
    const fields = [{ label: "Ticket coverage", value: `${records.length} ticket(s)` }, { label: "Ticket lifecycle distribution", value: filterDistribution(records, "ticket_lifecycle_status") }, { label: "Ticket phase distribution", value: filterDistribution(records, "ticket_phase") }];
    if (type === "Entry" || type === "Issue Group") return fields.filter((field) => field.value);
    ["Root-cause evidence", "Solution / recovery evidence", "Resolution plan", "Resolution version"].forEach((label) => fields.push({ label, value: filterValueSummary(records.map((record) => record.filter_detail_values?.[label] || "")) }));
    [["Resolution verification", "resolution_verification_status"], ["Source evidence coverage", "source_evidence_status"], ["Historical Shift evidence", "historical_shift_status"], ["Current postponement assessment", "current_postponement_status"], ["Current repair evidence", "repair_status"], ["Current delivery evidence", "delivery_status"]].forEach(([label, field]) => fields.push({ label, value: filterDistribution(records, field) }));
    ["Current progress assessment", "Evidence boundary"].forEach((label) => fields.push({ label, value: filterValueSummary(records.map((record) => record.filter_detail_values?.[label] || "")) }));
    return fields.filter((field) => field.value);
}
function filteredNodeEvidence(records) {
    const groups = new Map(); records.forEach((record) => (record.filter_detail_groups || []).forEach((group) => { if (!group.value) return; if (!groups.has(group.label)) groups.set(group.label, []); groups.get(group.label).push(`${record.representative_ticket_id}\n${group.value}`); }));
    return [...groups].map(([label, values]) => ({ label, value: values.slice(0, 3).join("\n\n---\n\n") + (values.length > 3 ? `\n\n${values.length - 3} additional evidence record(s) retained in Excel/JSON.` : "") }));
}
function filteredFunction(functionData, phases, from, through) {
    const records = functionData.scenario_records.filter((record) => {
        if (!phases.has(ticketPhaseCode(record.ticket_phase))) return false;
        const day = ticketCreationDay(record);
        return (!from && !through) || Boolean(day && (!from || day >= from) && (!through || day <= through));
    });
    const byTicket = new Map(records.map((record) => [record.representative_ticket_id, record]));
    const nodes = functionData.data_tree_nodes.flatMap((node) => {
        const members = (node.ticket_ids || []).map((ticketId) => byTicket.get(ticketId)).filter(Boolean);
        if (!members.length) return [];
        let question = node.question_or_solution; let label = node.visual_label;
        if (node.node_type === "Symptom Pattern" || node.node_type === "Trigger Pattern") {
            const field = node.node_type === "Symptom Pattern" ? "customer_symptom" : "trigger_condition";
            question = `${node.node_type === "Symptom Pattern" ? "Observed symptom patterns" : "Observed trigger patterns"}: ${filterValueSummary(members.map((record) => record[field]))}`;
            label = question.slice(0, 90);
        } else if (node.node_type === "Future Resolution Plan") {
            question = filterValueSummary(members.map((record) => record.filter_detail_values?.["Resolution plan"] || ""));
            label = `${members.length} ticket(s) | Recorded plans`;
        }
        return [{ ...node, question_or_solution: question, visual_label: label, source_ticket_count: members.length, source_scenario_count: members.length, source_scenario_ids: members.map((record) => record.scenario_id).join("; "), ticket_ids: members.map((record) => record.representative_ticket_id), detail_fields: filteredNodeDetails(members, node.node_type), detail_groups: node.node_type === "Entry" || node.node_type === "Issue Group" ? [] : filteredNodeEvidence(members) }];
    });
    return { ...functionData, scenario_records: records, data_tree_nodes: nodes, scenario_count: records.length, ticket_count: byTicket.size, source_ticket_mention_count: byTicket.size, node_count: nodes.length };
}
function applyTicketFilters() {
    const fromInput = document.getElementById("ticket-created-from"); const throughInput = document.getElementById("ticket-created-through");
    const from = fromInput.value; const through = throughInput.value; const status = document.getElementById("ticket-filter-status");
    throughInput.setCustomValidity("");
    if (from && through && from > through) { const message = "Created through must be on or after Created from."; throughInput.setCustomValidity(message); status.textContent = message; return; }
    const phases = new Set([...document.querySelectorAll('#ticket-phase-options input:checked')].map((input) => input.value));
    const selectedId = activeFunction?.service_function_id || document.getElementById("function-selector").value;
    visibleFunctions = catalog.functions.map((functionData) => filteredFunction(functionData, phases, from, through));
    activeFunction = visibleFunctions.find((functionData) => functionData.service_function_id === selectedId) || null;
    activeNode = null; populateFunctionSelector(document.getElementById("function-filter").value); selectFunction(document.getElementById("function-selector").value); initializeSummary();
    const tickets = visibleFunctions.reduce((total, functionData) => total + functionData.ticket_count, 0);
    const plans = visibleFunctions.reduce((total, functionData) => total + functionData.data_tree_nodes.filter((node) => node.node_type === "Future Resolution Plan").length, 0);
    status.textContent = `${tickets} of ${catalog.summary.field_ticket_count} tickets; ${plans} visible plan branches`;
}
function resetTicketFilters() {
    document.querySelectorAll('#ticket-phase-options input').forEach((input) => { input.checked = !(catalog.ticket_filters.excluded_phases || []).includes(input.value); });
    document.getElementById("ticket-created-from").value = catalog.ticket_filters.created_from || "";
    document.getElementById("ticket-created-through").value = catalog.ticket_filters.created_through || "";
    applyTicketFilters();
}
function initializeTicketFilters() {
    if (!catalog.ticket_filters) return false;
    document.getElementById("ticket-filters").hidden = false;
    const labels = new Map(); catalog.functions.forEach((functionData) => functionData.scenario_records.forEach((record) => { const code = ticketPhaseCode(record.ticket_phase); if (!labels.has(code)) labels.set(code, text(record.ticket_phase) || "Unknown phase"); }));
    const container = document.getElementById("ticket-phase-options");
    [...labels].sort(([left], [right]) => left.localeCompare(right)).forEach(([code, value]) => { const label = document.createElement("label"); const input = document.createElement("input"); input.type = "checkbox"; input.value = code; input.addEventListener("change", applyTicketFilters); label.append(input, document.createTextNode(value)); container.appendChild(label); });
    document.getElementById("ticket-filters").addEventListener("submit", (event) => event.preventDefault());
    ["ticket-created-from", "ticket-created-through"].forEach((id) => document.getElementById(id).addEventListener("change", applyTicketFilters));
    document.querySelectorAll("[data-phase-action]").forEach((button) => button.addEventListener("click", () => { document.querySelectorAll('#ticket-phase-options input').forEach((input) => { input.checked = button.dataset.phaseAction === "all"; }); applyTicketFilters(); }));
    document.getElementById("reset-ticket-filters").addEventListener("click", resetTicketFilters);
    resetTicketFilters(); return true;
}
function svgElement(name, attrs = {}) { const element = document.createElementNS(NS, name); Object.entries(attrs).forEach(([key, value]) => element.setAttribute(key, text(value))); return element; }
function createCell(row, value, className = "") { const cell = document.createElement("td"); cell.textContent = text(value); if (className) cell.className = className; row.appendChild(cell); }
function visibleSourceGraph() { return activeFunction && activeFunction.source_graphs[activeSourceIndex] || null; }
function normalizeNode(node, view) {
    if (view === "source") return { id: node.node_id, parent: node.parent_node_id, type: node.node_type, label: node.source_text, fullLabel: node.source_text, evidence: node.symptom || node.check_step || node.possible_cause, tickets: "", status: node.review_status, raw: node };
    return { id: node.node_id, parent: node.parent_node_id, type: node.node_type, label: node.visual_label || node.question_or_solution, fullLabel: node.question_or_solution, evidence: node.evidence_source || node.source_scenario_ids, tickets: node.source_ticket_count, status: node.solution_status || node.node_source, raw: node };
}
function activeNodes() { if (!activeFunction) return []; if (activeView === "source") { const graph = visibleSourceGraph(); return graph ? graph.nodes.map((node) => normalizeNode(node, "source")) : []; } return activeFunction.data_tree_nodes.map((node) => normalizeNode(node, "data")); }
function edgePairs(nodes, view) { if (view === "source") { const graph = visibleSourceGraph(); return graph ? graph.edges.map((edge) => [edge.from_node_id, edge.to_node_id]) : []; } return nodes.filter((node) => node.parent).map((node) => [node.parent, node.id]); }
function wrapLines(value, limit) { const characters = Array.from(text(value)); const lines = []; while (characters.length && lines.length < 3) lines.push(characters.splice(0, limit).join("")); return lines.length ? lines : [""]; }
function drawLabel(svg, value, x, y, limit) { const lines = wrapLines(value, limit); const label = svgElement("text", { x, y: y - (lines.length - 1) * 8, "text-anchor": "middle", "font-size": 12, fill: "#17242c", "pointer-events": "none" }); lines.forEach((line, index) => { const span = svgElement("tspan", { x, dy: index ? 15 : 0 }); span.textContent = line; label.appendChild(span); }); svg.appendChild(label); }
function layoutTree(nodes, edges) {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const children = new Map(); nodes.forEach((node) => { if (!children.has(node.id)) children.set(node.id, []); }); edges.forEach(([fromId, toId]) => { if (byId.has(fromId) && byId.has(toId)) children.get(fromId).push(toId); });
  const roots = nodes.filter((node) => !node.parent || !byId.has(node.parent)).map((node) => node.id);
  const depth = new Map(); const queue = []; roots.forEach((id) => { depth.set(id, 0); queue.push(id); });
  for (let index = 0; index < queue.length; index += 1) { const current = queue[index]; (children.get(current) || []).forEach((child) => { if (!depth.has(child)) { depth.set(child, (depth.get(current) || 0) + 1); queue.push(child); } }); }
  nodes.forEach((node) => { if (!depth.has(node.id)) depth.set(node.id, 0); });
  const levels = new Map(); nodes.forEach((node) => { const level = depth.get(node.id) || 0; if (!levels.has(level)) levels.set(level, []); levels.get(level).push(node); });
  const maxDepth = Math.max(0, ...levels.keys()); const maxRows = Math.max(1, ...Array.from(levels.values(), (items) => items.length));
  const width = Math.max(960, 160 + (maxDepth + 1) * 280); const height = Math.max(380, 110 + maxRows * 110); const positions = new Map();
  Array.from(levels.entries()).forEach(([level, items]) => { items.sort((left, right) => left.id.localeCompare(right.id)); const gap = height / (items.length + 1); items.forEach((node, index) => positions.set(node.id, { x: 140 + level * 280, y: gap * (index + 1) })); });
  return { byId, positions, width, height };
}
function renderDiagram() {
  const container = document.getElementById("diagram-scroll"); container.replaceChildren(); const nodes = activeNodes(); const graph = visibleSourceGraph();
  const sourceSelect = document.getElementById("source-selector"); sourceSelect.replaceChildren();
  if (!activeFunction) return;
  activeFunction.source_graphs.forEach((item, index) => { const option = document.createElement("option"); option.value = String(index); option.textContent = `${item.reference_tree_id || item.tree_id} | ${item.tree_name_en || item.tree_id}`; sourceSelect.appendChild(option); });
  sourceSelect.disabled = activeView === "data" || !activeFunction.source_graphs.length;
  if (activeFunction.source_graphs.length) sourceSelect.value = String(activeSourceIndex);
  if (activeView === "image") {
    document.getElementById("diagram-title").textContent = "Original after-sales source image";
    if (!graph) { const empty = document.createElement("p"); empty.className = "empty-state"; empty.textContent = "No supplied after-sales source image is mapped to this service function. The V12 evidence tree remains available."; container.appendChild(empty); return; }
    const image = document.createElement("img"); image.className = "source-image"; image.src = graph.source_asset || ""; image.alt = `${graph.tree_name_en || graph.tree_id} original after-sales source image`; container.appendChild(image); return;
  }
  document.getElementById("diagram-title").textContent = activeView === "source" ? "AI structured source tree" : "V12 evidence-derived decision tree";
    if (!nodes.length) { const empty = document.createElement("p"); empty.className = "empty-state"; empty.textContent = activeView === "source" ? "No source draft is available for this service function." : catalog.ticket_filters ? "No tickets match the selected phase and creation dates." : "No V12 decision-tree nodes are available."; container.appendChild(empty); return; }
  const edges = edgePairs(nodes, activeView); const layout = layoutTree(nodes, edges); const svg = svgElement("svg", { viewBox: `0 0 ${layout.width} ${layout.height}`, role: "img", "aria-label": document.getElementById("diagram-title").textContent });
  const defs = svgElement("defs"); const marker = svgElement("marker", { id: "tree-arrow", markerWidth: 8, markerHeight: 8, refX: 7, refY: 4, orient: "auto", markerUnits: "strokeWidth" }); marker.appendChild(svgElement("path", { d: "M 0 0 L 8 4 L 0 8 z", fill: "#576a73" })); defs.appendChild(marker); svg.appendChild(defs);
  edges.forEach(([fromId, toId]) => { const from = layout.positions.get(fromId); const to = layout.positions.get(toId); if (!from || !to) return; svg.appendChild(svgElement("line", { x1: from.x + 86, y1: from.y, x2: to.x - 86, y2: to.y, stroke: "#576a73", "stroke-width": 1.4, "marker-end": "url(#tree-arrow)" })); });
  nodes.forEach((node) => { const position = layout.positions.get(node.id); const group = svgElement("g", { tabindex: 0, role: "button", "aria-label": `${node.type}: ${node.label}`, cursor: "pointer" }); const color = activeView === "source" ? (sourceColors[node.type] || "#eceff1") : (dataColors[node.type] || "#eceff1"); group.appendChild(svgElement("rect", { x: position.x - 88, y: position.y - 42, width: 176, height: 84, rx: 3, fill: color, stroke: activeNode && activeNode.id === node.id ? "#b55a19" : "#607681", "stroke-width": activeNode && activeNode.id === node.id ? 3 : 1.2 })); svg.appendChild(group); drawLabel(svg, node.type, position.x, position.y - 19, 18); drawLabel(svg, node.label, position.x, position.y + 6, 21); drawLabel(svg, node.tickets ? `${node.tickets} tickets` : node.status, position.x, position.y + 28, 20); group.addEventListener("click", () => { activeNode = node; renderAll(); }); group.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); activeNode = node; renderAll(); } }); });
  container.appendChild(svg);
}
function appendDetailField(container, key, value) { const item = document.createElement("div"); const term = document.createElement("dt"); const definition = document.createElement("dd"); term.textContent = key; definition.textContent = text(value); item.append(term, definition); container.appendChild(item); }
function renderNodeDetail() {
    const detail = document.getElementById("node-detail"); detail.replaceChildren();
    const nodes = activeNodes(); const node = activeNode || nodes[0];
    if (!node) { const row = document.createElement("div"); row.textContent = "No node selected."; detail.appendChild(row); return; }
    const raw = node.raw || {};
    const standardFields = activeView === "source" ? [["Node", node.id], ["Source path", raw.source_path], ["Symptom", raw.symptom], ["Possible cause", raw.possible_cause], ["Check step", raw.check_step], ["Source solution", raw.solution], ["Escalation", raw.escalation_path], ["DTC", raw.dtc_keywords], ["Defect mode", raw.defect_mode], ["Review", raw.review_status]] : [["Node", node.id], ["Type", node.type], ["Prompt / solution", node.fullLabel || node.label], ["Evidence source", raw.evidence_source], ["Scenario IDs", raw.source_scenario_ids], ["Ticket-recorded solution status", raw.solution_status], ["Node source", raw.node_source]];
    const detailFields = activeView === "source" ? [] : (Array.isArray(raw.detail_fields) ? raw.detail_fields.map((item) => [text(item.label), text(item.value)]) : []);
    standardFields.concat(detailFields).forEach(([key, value]) => appendDetailField(detail, key, value));
    const detailGroups = activeView === "source" ? [] : (Array.isArray(raw.detail_groups) ? raw.detail_groups : []);
    detailGroups.forEach((group) => {
        if (!group || !text(group.value)) return;
        const item = document.createElement("div"); const disclosure = document.createElement("details");
        const summary = document.createElement("summary"); summary.textContent = text(group.label) || "Raw evidence";
        const content = document.createElement("div"); content.className = "detail-raw"; content.textContent = text(group.value);
        disclosure.append(summary, content); item.appendChild(disclosure); detail.appendChild(item);
    });
}
function renderNodeRows() { const body = document.getElementById("node-rows"); body.replaceChildren(); activeNodes().forEach((node) => { const row = document.createElement("tr"); row.tabIndex = 0; [node.id, node.parent, node.type, node.fullLabel || node.label, node.evidence, node.tickets, node.status].forEach((value, index) => createCell(row, value, index === 5 ? "count" : "")); row.addEventListener("click", () => { activeNode = node; renderAll(); }); body.appendChild(row); }); }
function renderScenarioRows() { const body = document.getElementById("scenario-rows"); body.replaceChildren(); (activeFunction ? activeFunction.scenario_records : []).forEach((scenario) => { const row = document.createElement("tr"); [scenario.scenario_id, scenario.problem_type, scenario.customer_symptom, scenario.trigger_condition, scenario.ticket_count, scenario.recorded_recovery_action, scenario.evidence_confidence, scenario.representative_ticket_id].forEach((value, index) => createCell(row, value, index === 4 ? "count" : "")); body.appendChild(row); }); }
function renderMeta() { const functionData = activeFunction; document.getElementById("meta-function").textContent = functionData.service_function_label; document.getElementById("meta-domain").textContent = functionData.service_domain || "Not classified"; document.getElementById("meta-scenarios").textContent = text(functionData.scenario_count); document.getElementById("meta-tickets").textContent = text(functionData.ticket_count); document.getElementById("meta-mentions").textContent = text(functionData.source_ticket_mention_count); document.getElementById("meta-source-graphs").textContent = functionData.source_graphs.length ? `${functionData.source_graphs.length} AI structured` : "No supplied source image"; const graph = visibleSourceGraph(); document.getElementById("review-note").textContent = graph ? `AI structured review: ${graph.source_path_node_count} source-path nodes and ${graph.source_path_edge_count} inferred parent-child edges. ${graph.review_limitations}` : "This function has no mapped original after-sales source image. Its displayed tree is the V12 evidence-derived tree; ticket-recorded recovery text is historical evidence, not an approved repair instruction."; }
function renderAll() { renderMeta(); renderDiagram(); renderNodeDetail(); renderNodeRows(); renderScenarioRows(); document.querySelectorAll(".tab").forEach((tab) => tab.setAttribute("aria-selected", String(tab.dataset.view === activeView))); }
function populateFunctionSelector(filterText = "") { const selector = document.getElementById("function-selector"); const selectedId = activeFunction ? activeFunction.service_function_id : ""; const filter = filterText.trim().toLocaleLowerCase(); selector.replaceChildren(); visibleFunctions.filter((item) => !filter || [item.service_function_id, item.service_function_label, item.service_domain].join(" ").toLocaleLowerCase().includes(filter)).forEach((item) => { const option = document.createElement("option"); option.value = item.service_function_id; option.textContent = `${item.service_function_label} | ${item.scenario_count} scenarios / ${item.ticket_count} tickets`; selector.appendChild(option); }); if (!selector.options.length) { const option = document.createElement("option"); option.value = ""; option.textContent = "No matching service function"; selector.appendChild(option); return; } selector.value = Array.from(selector.options).some((option) => option.value === selectedId) ? selectedId : selector.options[0].value; }
function selectFunction(serviceFunctionId) { activeFunction = visibleFunctions.find((item) => item.service_function_id === serviceFunctionId) || visibleFunctions[0] || null; activeSourceIndex = 0; activeNode = null; renderAll(); }
function initializeSummary() { const summary = { ...(catalog.summary || {}) }; if (catalog.ticket_filters) { summary.field_scenario_count = visibleFunctions.reduce((total, functionData) => total + functionData.scenario_count, 0); summary.field_ticket_count = visibleFunctions.reduce((total, functionData) => total + functionData.ticket_count, 0); } document.getElementById("summary-functions").textContent = text(summary.service_function_count); document.getElementById("summary-scenarios").textContent = text(summary.field_scenario_count); document.getElementById("summary-tickets").textContent = text(summary.field_ticket_count); document.getElementById("summary-image-functions").textContent = text(summary.image_backed_function_count); document.getElementById("summary-source-graphs").textContent = text(summary.ai_structured_source_graph_count); document.getElementById("summary-source-nodes").textContent = text(summary.ai_structured_source_node_count); }
document.getElementById("function-filter").addEventListener("input", (event) => { populateFunctionSelector(event.target.value); selectFunction(document.getElementById("function-selector").value); });
document.getElementById("function-selector").addEventListener("change", (event) => selectFunction(event.target.value));
document.getElementById("source-selector").addEventListener("change", (event) => { activeSourceIndex = Number(event.target.value || 0); activeNode = null; renderAll(); });
document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => { activeView = tab.dataset.view; activeNode = null; renderAll(); }));
if (!initializeTicketFilters()) { initializeSummary(); populateFunctionSelector(); selectFunction(document.getElementById("function-selector").value); }
</script>
</body>
</html>
"""
    return template.replace("__CATALOG_DATA__", serialized_catalog)


def write_all_function_catalog_artifacts(
    *, catalog: dict[str, Any], output_html: Path, output_json: Path
) -> None:
    """Write the all-function catalog as a standalone HTML page and JSON payload."""
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    output_html.write_text(render_all_function_catalog_html(catalog), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build an all-function Phase 09 after-sales decision-tree catalog."
    )
    parser.add_argument(
        "--scenario-workbook", type=Path, default=DEFAULT_SCENARIO_WORKBOOK
    )
    parser.add_argument("--knowledge-base", type=Path, default=DEFAULT_KNOWLEDGE_BASE)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--extracted-directory", type=Path, default=DEFAULT_EXTRACTED_DIRECTORY)
    parser.add_argument("--output-html", type=Path, default=DEFAULT_OUTPUT_HTML)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    args = parser.parse_args(argv)

    catalog = build_all_function_catalog_from_files(
        scenario_workbook_path=args.scenario_workbook,
        knowledge_base_path=args.knowledge_base,
        registry_path=args.registry,
        manifest_path=args.manifest,
        extracted_directory=args.extracted_directory,
    )
    write_all_function_catalog_artifacts(
        catalog=catalog,
        output_html=args.output_html,
        output_json=args.output_json,
    )
    print(f"Saved HTML: {args.output_html}")
    print(f"Saved JSON: {args.output_json}")
    print(f"Service functions: {catalog['summary']['service_function_count']}")
    print(
        "AI structured source graphs: "
        f"{catalog['summary']['ai_structured_source_graph_count']}"
    )
    print(f"Unresolved references: {catalog['summary']['unresolved_reference_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())