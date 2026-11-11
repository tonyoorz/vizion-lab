#!/usr/bin/env python3
"""Build one integrated diagnosis tree for every Phase 09 service function.

Scenarios are sibling cases in a shared service-function decision tree. They
are grouped by an existing after-sales complaint route when an experience path
matches, otherwise by an observed field-failure route. This preserves exact
scenario evidence without inventing causal links between independent tickets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
import sys
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.analytics.phase09_scenario_diagnosis_tree_catalog import (
    DEFAULT_EXTRACTED_DIRECTORY,
    DEFAULT_KNOWLEDGE_BASE,
    DEFAULT_MANIFEST,
    DEFAULT_REGISTRY,
    DEFAULT_SCENARIO_WORKBOOK,
    DEFAULT_SOURCE_WORKBOOK,
    build_scenario_diagnosis_catalog_from_files,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
PHASE09_EXPORT_DIR = REPO_ROOT / "exports" / "idcevo_phase09"
DEFAULT_OUTPUT_HTML = (
    PHASE09_EXPORT_DIR
    / "IDCEVO_Phase09_All_Function_Service_Diagnosis_Tree_Catalog_V3.html"
)
DEFAULT_OUTPUT_JSON = (
    PHASE09_EXPORT_DIR
    / "IDCEVO_Phase09_All_Function_Service_Diagnosis_Tree_Catalog_V3.json"
)

_BRANCH_PRIORITY = {
    "after_sales_experience_source_path": 0,
    "ticket_recorded_rca_candidate": 1,
    "evidence_collection_template": 2,
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _integer(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "|".join(_text(part).casefold() for part in parts)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:12].upper()
    return f"{prefix}-{digest}"


def _branch_sort_key(branch: dict[str, Any]) -> tuple[int, float, float, str]:
    basis = _text(branch.get("basis"))
    ticket_evidence = branch.get("ticket_evidence")
    if not isinstance(ticket_evidence, dict):
        ticket_evidence = {}
    return (
        _BRANCH_PRIORITY.get(basis, 99),
        -float(branch.get("match_score", 0) or 0),
        -float(ticket_evidence.get("support_ratio", 0) or 0),
        _text(branch.get("branch_id")),
    )


def _primary_branch(path: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    branches = [
        branch
        for branch in path.get("diagnostic_branches", [])
        if isinstance(branch, dict)
    ]
    if not branches:
        return (
            {
                "branch_id": f"EVIDENCE-{_text(path.get('scenario_id'))}",
                "basis": "evidence_collection_template",
                "status": "no_verified_causal_evidence",
                "possible_cause": "No verified technical cause recorded",
                "check_steps": "Collect scenario evidence before diagnosis.",
                "resolution": "",
                "resolution_status": "no_recorded_recovery",
                "escalation": "Prepare an engineering evidence package",
                "source_reference": {},
                "ticket_evidence": {},
            },
            [],
        )
    ordered = sorted(branches, key=_branch_sort_key)
    return ordered[0], ordered[1:]


def _source_route_label(branch: dict[str, Any]) -> tuple[str, str, str]:
    source_reference = branch.get("source_reference")
    if not isinstance(source_reference, dict):
        source_reference = {}
    source_path = _text(source_reference.get("source_path"))
    segments = [segment.strip() for segment in source_path.split(" > ") if segment.strip()]
    route_name = segments[1] if len(segments) > 1 else (segments[-1] if segments else "")
    if not route_name:
        route_name = "Existing after-sales route"
    route_id = _stable_id(
        "ROUTE-SOURCE",
        source_reference.get("source_tree_id"),
        route_name,
    )
    return route_id, f"After-sales route: {route_name}", source_path


def _field_route_label(path: dict[str, Any]) -> tuple[str, str]:
    problem_type = _text(path.get("problem_type")) or "Other observed field failure"
    return (
        _stable_id("ROUTE-FIELD", problem_type),
        f"Observed field-failure route: {problem_type}",
    )


def _source_references(function: dict[str, Any]) -> list[dict[str, Any]]:
    references = []
    for graph in function.get("source_graphs", []):
        if not isinstance(graph, dict):
            continue
        references.append({
            "tree_id": _text(graph.get("tree_id")),
            "reference_tree_id": _text(graph.get("reference_tree_id")),
            "tree_name_cn": _text(graph.get("tree_name_cn")),
            "tree_name_en": _text(graph.get("tree_name_en")),
            "source_asset": _text(graph.get("source_asset")),
            "source_file": _text(graph.get("source_file")),
            "tree_version": _text(graph.get("tree_version")),
            "graph_status": _text(graph.get("graph_status")),
            "source_path_node_count": _integer(graph.get("source_path_node_count")),
            "source_path_edge_count": _integer(graph.get("source_path_edge_count")),
        })
    return sorted(
        references,
        key=lambda reference: (
            reference["tree_id"], reference["reference_tree_id"],
        ),
    )


def _compact_source_reference(value: Any) -> dict[str, Any]:
    source_reference = value if isinstance(value, dict) else {}
    return {
        "reference_tree_id": _text(source_reference.get("reference_tree_id")),
        "source_tree_id": _text(source_reference.get("source_tree_id")),
        "source_node_id": _text(source_reference.get("source_node_id")),
        "source_path": _text(source_reference.get("source_path")),
        "source_asset": _text(source_reference.get("source_asset")),
        "source_match_score": source_reference.get("source_match_score", ""),
    }


def _compact_branch(branch: dict[str, Any]) -> dict[str, Any]:
    ticket_evidence = branch.get("ticket_evidence")
    if not isinstance(ticket_evidence, dict):
        ticket_evidence = {}
    return {
        "branch_id": _text(branch.get("branch_id")),
        "basis": _text(branch.get("basis")),
        "status": _text(branch.get("status")),
        "match_confidence": _text(branch.get("match_confidence")),
        "match_score": branch.get("match_score", 0) or 0,
        "possible_cause": _text(branch.get("possible_cause")),
        "check_steps": _text(branch.get("check_steps")),
        "resolution": _text(branch.get("resolution")),
        "resolution_status": _text(branch.get("resolution_status")),
        "escalation": _text(branch.get("escalation")),
        "source_reference": _compact_source_reference(
            branch.get("source_reference")
        ),
        "ticket_evidence": {
            "support_ticket_count": _integer(
                ticket_evidence.get("support_ticket_count")
            ),
            "support_ratio": ticket_evidence.get("support_ratio", ""),
            "ticket_ids": list(ticket_evidence.get("ticket_ids") or []),
        },
    }


def _new_node(
    *,
    node_id: str,
    parent_node_id: str,
    node_type: str,
    label: str,
    basis: str,
    status: str,
    scenario_id: str = "",
    source_ticket_count: int = 0,
    source_reference: dict[str, Any] | None = None,
    next_node_id: str = "",
    alternate_branch_count: int = 0,
) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "parent_node_id": parent_node_id,
        "node_type": node_type,
        "label": label,
        "basis": basis,
        "status": status,
        "scenario_id": scenario_id,
        "source_ticket_count": source_ticket_count,
        "source_reference": source_reference or {},
        "next_node_id": next_node_id,
        "alternate_branch_count": alternate_branch_count,
    }


def build_service_function_tree(function: dict[str, Any]) -> dict[str, Any]:
    """Create one tree that contains every scenario in one service function."""
    service_function_id = _text(function.get("service_function_id"))
    if not service_function_id:
        raise ValueError("service_function_id is required")
    service_label = _text(function.get("service_function_label")) or _text(
        function.get("service_function")
    )
    scenario_paths = [
        path
        for path in function.get("scenario_paths", [])
        if isinstance(path, dict) and _text(path.get("scenario_id"))
    ]
    route_records: dict[str, dict[str, Any]] = {}
    scenario_records = []
    for path in scenario_paths:
        primary_branch_raw, alternate_branches_raw = _primary_branch(path)
        primary_branch = _compact_branch(primary_branch_raw)
        alternate_branches = [
            _compact_branch(branch) for branch in alternate_branches_raw
        ]
        if _text(primary_branch.get("basis")) == "after_sales_experience_source_path":
            route_id, route_label, source_path = _source_route_label(primary_branch)
            route_type = "after_sales_experience_route"
            source_reference = primary_branch.get("source_reference")
            if not isinstance(source_reference, dict):
                source_reference = {}
        else:
            route_id, route_label = _field_route_label(path)
            source_path = ""
            route_type = "field_failure_route"
            source_reference = {}
        route = route_records.setdefault(
            route_id,
            {
                "route_id": route_id,
                "route_type": route_type,
                "label": route_label,
                "source_path": source_path,
                "source_reference": source_reference,
                "scenario_ids": [],
                "ticket_count": 0,
            },
        )
        scenario_id = _text(path.get("scenario_id"))
        route["scenario_ids"].append(scenario_id)
        route["ticket_count"] += _integer(path.get("source_ticket_count"))
        scenario_records.append({
            "scenario_id": scenario_id,
            "route_id": route_id,
            "customer_symptom": _text(path.get("customer_symptom")),
            "trigger_condition": _text(path.get("trigger_condition")),
            "observed_behaviour": _text(path.get("observed_behaviour")),
            "expected_behaviour": _text(path.get("expected_behaviour")),
            "representative_ticket_id": _text(path.get("representative_ticket_id")),
            "source_ticket_ids": list(path.get("source_ticket_ids") or []),
            "source_ticket_count": _integer(path.get("source_ticket_count")),
            "problem_type": _text(path.get("problem_type")),
            "risk_band": _text(path.get("risk_band")),
            "risk_index": path.get("risk_index") or "",
            "evidence_confidence": _text(path.get("evidence_confidence")),
            "primary_branch": primary_branch,
            "alternate_branches": alternate_branches,
        })

    routes = sorted(
        route_records.values(),
        key=lambda route: (
            0 if route["route_type"] == "after_sales_experience_route" else 1,
            -route["ticket_count"],
            route["label"],
        ),
    )
    for route in routes:
        route["scenario_ids"].sort(
            key=lambda scenario_id: next(
                (
                    (-record["source_ticket_count"], record["scenario_id"])
                    for record in scenario_records
                    if record["scenario_id"] == scenario_id
                ),
                (0, scenario_id),
            )
        )
        route["scenario_count"] = len(route["scenario_ids"])

    nodes = [
        _new_node(
            node_id="ROOT",
            parent_node_id="",
            node_type="Function Entry",
            label=service_label,
            basis="service_function_mapping",
            status="shared_after_sales_tree_root",
            source_ticket_count=sum(
                record["source_ticket_count"] for record in scenario_records
            ),
        )
    ]
    edges = []
    for route in routes:
        nodes.append(_new_node(
            node_id=route["route_id"],
            parent_node_id="ROOT",
            node_type="Complaint Route",
            label=route["label"],
            basis=route["route_type"],
            status=(
                "after_sales_experience_route"
                if route["route_type"] == "after_sales_experience_route"
                else "data_derived_field_route"
            ),
            source_ticket_count=route["ticket_count"],
            source_reference=route["source_reference"],
        ))
        edges.append({
            "from_node_id": "ROOT",
            "to_node_id": route["route_id"],
            "relation": "routes_customer_complaint",
        })

    records_by_route: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in scenario_records:
        records_by_route[record["route_id"]].append(record)
    for route in routes:
        for record in sorted(
            records_by_route[route["route_id"]],
            key=lambda item: (-item["source_ticket_count"], item["scenario_id"]),
        ):
            scenario_id = record["scenario_id"]
            scenario_node_id = f"SC-{scenario_id}"
            trigger_node_id = f"TRIGGER-{scenario_id}"
            cause_node_id = f"CAUSE-{scenario_id}"
            check_node_id = f"CHECK-{scenario_id}"
            outcome_node_id = f"OUTCOME-{scenario_id}"
            branch = record["primary_branch"]
            basis = _text(branch.get("basis"))
            status = _text(branch.get("status"))
            source_reference = branch.get("source_reference")
            if not isinstance(source_reference, dict):
                source_reference = {}
            nodes.extend([
                _new_node(
                    node_id=scenario_node_id,
                    parent_node_id=route["route_id"],
                    node_type="Failure Scenario",
                    label=record["customer_symptom"],
                    basis="v12_exact_customer_scenario",
                    status="field_scenario_evidence",
                    scenario_id=scenario_id,
                    source_ticket_count=record["source_ticket_count"],
                    source_reference=source_reference,
                    next_node_id=trigger_node_id,
                    alternate_branch_count=len(record["alternate_branches"]),
                ),
                _new_node(
                    node_id=trigger_node_id,
                    parent_node_id=scenario_node_id,
                    node_type="Trigger Condition",
                    label=record["trigger_condition"],
                    basis="v12_exact_trigger_condition",
                    status="field_scenario_evidence",
                    scenario_id=scenario_id,
                    source_ticket_count=record["source_ticket_count"],
                    next_node_id=cause_node_id,
                ),
                _new_node(
                    node_id=cause_node_id,
                    parent_node_id=trigger_node_id,
                    node_type="Possible Cause",
                    label=_text(branch.get("possible_cause"))
                    or "No verified technical cause recorded",
                    basis=basis,
                    status=status,
                    scenario_id=scenario_id,
                    source_ticket_count=record["source_ticket_count"],
                    source_reference=source_reference,
                    next_node_id=check_node_id,
                ),
                _new_node(
                    node_id=check_node_id,
                    parent_node_id=cause_node_id,
                    node_type="Check Steps",
                    label=_text(branch.get("check_steps")),
                    basis=basis,
                    status=status,
                    scenario_id=scenario_id,
                    source_ticket_count=record["source_ticket_count"],
                    source_reference=source_reference,
                    next_node_id=outcome_node_id,
                ),
                _new_node(
                    node_id=outcome_node_id,
                    parent_node_id=check_node_id,
                    node_type="Solution / Escalation",
                    label=_text(branch.get("resolution"))
                    or _text(branch.get("escalation")),
                    basis=basis,
                    status=_text(branch.get("resolution_status")) or status,
                    scenario_id=scenario_id,
                    source_ticket_count=record["source_ticket_count"],
                    source_reference=source_reference,
                ),
            ])
            edges.extend([
                {
                    "from_node_id": route["route_id"],
                    "to_node_id": scenario_node_id,
                    "relation": "contains_exact_failure_scenario",
                },
                {
                    "from_node_id": scenario_node_id,
                    "to_node_id": trigger_node_id,
                    "relation": "occurs_when",
                },
                {
                    "from_node_id": trigger_node_id,
                    "to_node_id": cause_node_id,
                    "relation": "investigate_as",
                },
                {
                    "from_node_id": cause_node_id,
                    "to_node_id": check_node_id,
                    "relation": "check_with",
                },
                {
                    "from_node_id": check_node_id,
                    "to_node_id": outcome_node_id,
                    "relation": "resolve_or_escalate",
                },
            ])
    return {
        "tree_id": f"DTU-{service_function_id}",
        "service_function_id": service_function_id,
        "service_function_label": service_label,
        "scenario_count": len(scenario_records),
        "ticket_count": sum(
            record["source_ticket_count"] for record in scenario_records
        ),
        "routes": routes,
        "scenario_cases": scenario_records,
        "nodes": nodes,
        "edges": edges,
        "tree_generation": "service_function_shared_scenario_tree_v3",
        "relationship_policy": (
            "Scenarios are sibling cases under a shared customer-complaint route. "
            "The tree shows diagnostic progression within each scenario, not a "
            "chronological or causal transition from one scenario to another."
        ),
    }


def build_service_function_tree_catalog_from_scenario_catalog(
    scenario_catalog: dict[str, Any],
) -> dict[str, Any]:
    """Transform V2 scenario paths into one unified tree per service function."""
    functions = []
    for function in scenario_catalog.get("functions", []):
        if not isinstance(function, dict):
            continue
        unified_tree = build_service_function_tree(function)
        functions.append({
            "service_function_id": _text(function.get("service_function_id")),
            "service_function": _text(function.get("service_function")),
            "service_function_label": _text(function.get("service_function_label")),
            "service_domain": _text(function.get("service_domain")),
            "catalog_status": _text(function.get("catalog_status")),
            "source_references": _source_references(function),
            "unified_tree": unified_tree,
        })
    functions.sort(
        key=lambda function: (
            function["service_domain"].casefold(),
            function["service_function_label"].casefold(),
        )
    )
    trees = [function["unified_tree"] for function in functions]
    nodes = [node for tree in trees for node in tree["nodes"]]
    source_routed_scenarios = [
        record
        for tree in trees
        for record in tree["scenario_cases"]
        if _text(record["primary_branch"].get("basis"))
        == "after_sales_experience_source_path"
    ]
    ticket_rca_scenarios = [
        record
        for tree in trees
        for record in tree["scenario_cases"]
        if _text(record["primary_branch"].get("basis"))
        == "ticket_recorded_rca_candidate"
    ]
    evidence_only_scenarios = [
        record
        for tree in trees
        for record in tree["scenario_cases"]
        if _text(record["primary_branch"].get("basis"))
        == "evidence_collection_template"
    ]
    return {
        "schema_version": "phase09-service-function-diagnosis-tree-catalog-v3",
        "data_policy": {
            "tree_scope_policy": (
                "Every V12 field scenario appears exactly once in its service-function "
                "tree. Shared routes organize parallel customer cases; they do not "
                "assert that one scenario caused or followed another."
            ),
            "source_experience_policy": (
                "An after-sales source route is used when its extracted experience leaf "
                "is an AI-matched reference candidate. The original source image remains "
                "available for traceability."
            ),
            "ticket_evidence_policy": (
                "Ticket RCA is a review-required recorded candidate. Ticket recovery can "
                "be a workaround and is not an approved repair instruction."
            ),
            "evidence_gap_policy": (
                "Evidence-only branches intentionally show collection and escalation rather "
                "than inventing causes, checks, or repairs."
            ),
        },
        "summary": {
            "service_function_count": len(functions),
            "service_tree_count": len(trees),
            "scenario_count": sum(tree["scenario_count"] for tree in trees),
            "field_ticket_count": sum(tree["ticket_count"] for tree in trees),
            "tree_node_count": len(nodes),
            "after_sales_route_count": sum(
                route["route_type"] == "after_sales_experience_route"
                for tree in trees
                for route in tree["routes"]
            ),
            "field_failure_route_count": sum(
                route["route_type"] == "field_failure_route"
                for tree in trees
                for route in tree["routes"]
            ),
            "source_routed_scenario_count": len(source_routed_scenarios),
            "ticket_rca_primary_scenario_count": len(ticket_rca_scenarios),
            "evidence_only_primary_scenario_count": len(evidence_only_scenarios),
            "source_reference_count": sum(
                len(function["source_references"]) for function in functions
            ),
        },
        "functions": functions,
    }


def build_service_function_tree_catalog_from_files(
    *,
    scenario_workbook_path: Path,
    knowledge_base_path: Path,
    source_workbook_path: Path,
    registry_path: Path,
    manifest_path: Path,
    extracted_directory: Path,
) -> dict[str, Any]:
    """Build V3 from the same current source layers used by V2."""
    scenario_catalog = build_scenario_diagnosis_catalog_from_files(
        scenario_workbook_path=scenario_workbook_path,
        knowledge_base_path=knowledge_base_path,
        source_workbook_path=source_workbook_path,
        registry_path=registry_path,
        manifest_path=manifest_path,
        extracted_directory=extracted_directory,
    )
    return build_service_function_tree_catalog_from_scenario_catalog(scenario_catalog)


def _serialized_html_data(value: Any) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def render_service_function_tree_catalog_html(catalog: dict[str, Any]) -> str:
    """Render one expandable, shared diagnosis tree for each service function."""
    serialized_catalog = _serialized_html_data(catalog)
    template = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>All-Function Unified Service Diagnosis Trees</title>
<style>
:root { color:#17242c; background:#edf1ef; font-family:Aptos,"Noto Sans SC","Microsoft YaHei",sans-serif; }
* { box-sizing:border-box; }
html,body { overflow-x:hidden; }
body { margin:0; }
main { max-width:1740px; margin:0 auto; padding:26px 28px 48px; }
header { border-bottom:4px solid #187166; padding-bottom:17px; }
h1 { margin:0; color:#123c4a; font-size:28px; letter-spacing:0; }
p { line-height:1.5; }
.subtitle { margin:7px 0 0; color:#52616b; font-size:15px; }
.summary { display:grid; grid-template-columns:repeat(7,minmax(110px,1fr)); gap:1px; margin-top:20px; border:1px solid #c7d0d6; background:#c7d0d6; }
.summary div { min-width:0; min-height:66px; padding:10px 12px; background:#fff; }
.summary span { display:block; color:#61717a; font-size:12px; }
.summary strong { display:block; margin-top:5px; color:#173c49; font-size:17px; overflow-wrap:anywhere; }
.controls { display:grid; grid-template-columns:minmax(190px,.52fr) minmax(250px,.82fr) minmax(250px,.86fr) minmax(340px,1.6fr); gap:14px; margin:20px 0 14px; }
label { min-width:0; color:#33414b; font-size:13px; font-weight:700; }
input,select { display:block; width:100%; min-height:42px; margin-top:6px; padding:8px 11px; border:1px solid #82949c; border-radius:4px; background:#fff; color:#17242c; font:inherit; }
.tree-meta { display:grid; grid-template-columns:repeat(6,minmax(120px,1fr)); gap:1px; border:1px solid #c7d0d6; background:#c7d0d6; }
.tree-meta div { min-width:0; min-height:69px; padding:10px 12px; background:#fff; }
.tree-meta span { display:block; color:#61717a; font-size:12px; }
.tree-meta strong { display:block; margin-top:5px; color:#173c49; font-size:14px; line-height:1.25; overflow-wrap:anywhere; }
.governance { margin:14px 0; padding:11px 13px; border-left:4px solid #cb8528; background:#fff4df; color:#5b3a0c; font-size:13px; }
.tabs { display:flex; gap:1px; margin-top:18px; overflow:auto; border-bottom:2px solid #187166; }
.tab { flex:0 0 auto; min-height:42px; padding:8px 14px; border:1px solid #9aacb2; border-bottom:0; background:#e8eeee; color:#263d47; font:inherit; font-weight:700; cursor:pointer; }
.tab[aria-selected="true"] { border-color:#187166; background:#187166; color:#fff; }
.workspace { min-width:0; display:grid; grid-template-columns:minmax(0,1fr) minmax(310px,.38fr); gap:16px; margin-top:14px; align-items:start; }
.diagram-panel,.detail-panel,.table-shell { min-width:0; border:1px solid #b9c6cc; background:#fff; }
.panel-head { display:flex; justify-content:space-between; gap:12px; align-items:center; padding:10px 12px; border-bottom:1px solid #c7d0d6; background:#eaf1f0; color:#173c49; font-size:13px; font-weight:700; }
.panel-head select { width:min(360px,68%); min-height:32px; margin:0; padding:4px 8px; }
.diagram-scroll { min-width:0; overflow:auto; min-height:500px; max-height:820px; }
svg { display:block; min-width:1980px; width:100%; height:auto; }
.source-image { display:block; width:100%; min-width:940px; height:auto; }
.empty-state { padding:28px; color:#536670; line-height:1.5; }
.detail-panel { position:sticky; top:12px; }
.detail-panel dl { margin:0; }
.detail-panel div { padding:10px 12px; border-bottom:1px solid #d4dde0; }
.detail-panel div:last-child { border-bottom:0; }
.detail-panel dt { color:#61717a; font-size:12px; }
.detail-panel dd { margin:4px 0 0; color:#1b3038; font-size:13px; line-height:1.45; white-space:pre-wrap; overflow-wrap:anywhere; }
.section-title { margin:25px 0 9px; color:#123c4a; font-size:18px; }
.table-shell { overflow:auto; max-height:560px; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th,td { min-width:0; padding:8px 9px; border:1px solid #c7d0d6; text-align:left; vertical-align:top; line-height:1.4; overflow-wrap:anywhere; }
th { position:sticky; top:0; z-index:1; background:#123c4a; color:#fff; white-space:nowrap; }
td.count { text-align:right; white-space:nowrap; }
tr.selected td { background:#fff4df; }
@media (max-width:980px) { main { padding:20px 16px 34px; } h1 { font-size:23px; } .summary,.tree-meta { grid-template-columns:repeat(2,minmax(0,1fr)); } .controls,.workspace { grid-template-columns:1fr; } .detail-panel { position:static; } .diagram-scroll { min-height:380px; } }
</style>
</head>
<body>
<main>
  <header>
    <h1>All-Function Unified Service Diagnosis Trees</h1>
    <p class="subtitle">One shared after-sales logic tree per Service Function. All field scenarios are sibling cases under customer-complaint routes; selecting a case expands its exact Trigger -> Possible Cause -> Check Steps -> Solution / Escalation path.</p>
  </header>
  <section class="summary" aria-label="Catalog summary">
    <div><span>Service functions</span><strong id="summary-functions"></strong></div>
    <div><span>Unified trees</span><strong id="summary-trees"></strong></div>
    <div><span>Failure scenarios</span><strong id="summary-scenarios"></strong></div>
    <div><span>Field tickets</span><strong id="summary-tickets"></strong></div>
    <div><span>After-sales routes</span><strong id="summary-source-routes"></strong></div>
    <div><span>Field-failure routes</span><strong id="summary-field-routes"></strong></div>
    <div><span>Tree nodes</span><strong id="summary-nodes"></strong></div>
  </section>
  <section class="controls">
    <label for="function-filter">Find a function<input id="function-filter" type="search" placeholder="Function, domain, or ID"></label>
    <label for="function-selector">Service function<select id="function-selector"></select></label>
    <label for="route-selector">Complaint route<select id="route-selector"></select></label>
    <label for="scenario-selector">Failure scenario<select id="scenario-selector"></select></label>
  </section>
  <section class="tree-meta">
    <div><span>Function</span><strong id="meta-function"></strong></div>
    <div><span>Routes</span><strong id="meta-routes"></strong></div>
    <div><span>All scenarios</span><strong id="meta-scenarios"></strong></div>
    <div><span>All tickets</span><strong id="meta-tickets"></strong></div>
    <div><span>Visible scenarios</span><strong id="meta-visible"></strong></div>
    <div><span>Selected case</span><strong id="meta-selected"></strong></div>
  </section>
  <p class="governance" id="governance-note"></p>
  <nav class="tabs" role="tablist" aria-label="Tree views">
    <button class="tab" type="button" role="tab" data-view="unified" aria-selected="true">Unified Service Function Tree</button>
    <button class="tab" type="button" role="tab" data-view="image" aria-selected="false">Original After-Sales Source Image</button>
  </nav>
  <section class="workspace">
    <section class="diagram-panel">
      <header class="panel-head"><span id="diagram-title"></span><select id="source-selector" aria-label="After-sales source reference"></select></header>
      <div class="diagram-scroll" id="diagram-scroll"></div>
    </section>
    <aside class="detail-panel"><header class="panel-head">Selected scenario route</header><dl id="detail-panel"></dl></aside>
  </section>
  <h2 class="section-title">All scenarios in the selected Service Function</h2>
  <section class="table-shell"><table><thead><tr><th>Complaint route</th><th>Scenario</th><th>Customer symptom</th><th>Trigger</th><th>Primary diagnostic basis</th><th>Tickets</th><th>Alternate routes</th></tr></thead><tbody id="scenario-rows"></tbody></table></section>
  <h2 class="section-title">Selected diagnostic progression</h2>
  <section class="table-shell"><table><thead><tr><th>Stage</th><th>Content</th><th>Basis</th><th>Status</th></tr></thead><tbody id="stage-rows"></tbody></table></section>
</main>
<script id="catalog-data" type="application/json">__CATALOG_DATA__</script>
<script>
const catalog = JSON.parse(document.getElementById("catalog-data").textContent);
const NS = "http://www.w3.org/2000/svg";
const colors = { "Function Entry":"#d7ead1", "Complaint Route":"#b8d7a9", "Failure Scenario":"#d8e8f5", "Trigger Condition":"#fff0c8", "Possible Cause":"#f3dfc5", "Check Steps":"#dbece7", "Solution / Escalation":"#e4e9ec" };
let activeFunction = null;
let activeTree = null;
let activeRouteId = "";
let activeScenarioId = "";
let activeView = "unified";
let activeSourceIndex = 0;
function text(value) { return String(value || ""); }
function svgElement(name, attributes = {}) { const element = document.createElementNS(NS, name); Object.entries(attributes).forEach(([key,value]) => element.setAttribute(key,text(value))); return element; }
function wrapLines(value, limit) { const characters=Array.from(text(value)); const lines=[]; while(characters.length && lines.length<3) lines.push(characters.splice(0,limit).join("")); return lines.length ? lines : [""]; }
function drawText(svg,value,x,y,limit,color="#17242c",fontSize=12) { const lines=wrapLines(value,limit); const label=svgElement("text",{x,y:y-(lines.length-1)*8,"text-anchor":"middle","font-size":fontSize,fill:color}); lines.forEach((line,index)=>{const span=svgElement("tspan",{x,dy:index?15:0});span.textContent=line;label.appendChild(span);});svg.appendChild(label); }
function arrow(svg,from,to,markerId) { svg.appendChild(svgElement("line",{x1:from.x+from.right,y1:from.y,x2:to.x-to.left,y2:to.y,stroke:"#536670","stroke-width":1.25,"marker-end":`url(#${markerId})`})); }
function nodeShape(svg,node,position,onSelect) { const group=svgElement("g",{tabindex:0,role:"button",cursor:onSelect?"pointer":"default","aria-label":`${node.node_type}: ${node.label}`});const title=svgElement("title");title.textContent=`${node.node_type}: ${node.label}`;group.appendChild(title);group.appendChild(svgElement("rect",{x:position.x-position.left,y:position.y-42,width:position.left*2,height:84,rx:4,fill:colors[node.node_type]||"#eceff1",stroke:node.scenario_id===activeScenarioId?"#b55a19":"#59717c","stroke-width":node.scenario_id===activeScenarioId?3:1.2}));svg.appendChild(group);drawText(svg,node.node_type,position.x,position.y-20,23,"#52616b",10);drawText(svg,node.label,position.x,position.y+4,node.node_type==="Failure Scenario"?31:24);if(node.source_ticket_count)drawText(svg,`${node.source_ticket_count} tickets`,position.x,position.y+29,23,"#52616b",10);if(onSelect){group.addEventListener("click",onSelect);group.addEventListener("keydown",event=>{if(event.key==="Enter"||event.key===" "){event.preventDefault();onSelect();}});} }
function nodeById(nodeId) { return activeTree.nodes.find(node=>node.node_id===nodeId)||null; }
function selectedScenarioNode() { return activeTree.nodes.find(node=>node.node_type==="Failure Scenario"&&node.scenario_id===activeScenarioId)||null; }
function visibleRoutes() { return activeRouteId ? activeTree.routes.filter(route=>route.route_id===activeRouteId) : activeTree.routes; }
function scenarioNodesForRoutes(routes) { const routeIds=new Set(routes.map(route=>route.route_id)); return activeTree.nodes.filter(node=>node.node_type==="Failure Scenario"&&routeIds.has(node.parent_node_id)); }
function downstreamNodes(scenarioNode) { if(!scenarioNode)return [];const result=[];let current=nodeById(scenarioNode.next_node_id);while(current){result.push(current);current=nodeById(current.next_node_id);}return result; }
function renderUnifiedTree() { const container=document.getElementById("diagram-scroll");container.replaceChildren();document.getElementById("diagram-title").textContent="Unified Service Function Tree";if(!activeTree){return;}const routes=visibleRoutes();const scenarios=scenarioNodesForRoutes(routes);if(!scenarios.length){const empty=document.createElement("p");empty.className="empty-state";empty.textContent="No V12 field scenarios are mapped to this route. The function entry remains available.";container.appendChild(empty);return;}const byRoute=new Map(routes.map(route=>[route.route_id,[]]));scenarios.forEach(node=>byRoute.get(node.parent_node_id)?.push(node));routes.forEach(route=>byRoute.get(route.route_id).sort((left,right)=>right.source_ticket_count-left.source_ticket_count||left.scenario_id.localeCompare(right.scenario_id)));const totalRows=scenarios.length+routes.length;const height=Math.max(500,130+totalRows*84);const width=2020;const svg=svgElement("svg",{viewBox:`0 0 ${width} ${height}`,role:"img","aria-label":"Unified service function diagnosis tree"});const defs=svgElement("defs");const marker=svgElement("marker",{id:"tree-arrow",markerWidth:8,markerHeight:8,refX:7,refY:4,orient:"auto",markerUnits:"strokeWidth"});marker.appendChild(svgElement("path",{d:"M 0 0 L 8 4 L 0 8 z",fill:"#536670"}));defs.appendChild(marker);svg.appendChild(defs);const root=nodeById("ROOT");const rootPosition={x:110,y:height/2,left:94,right:94};const routePositions=new Map();const scenarioPositions=new Map();let currentY=74;routes.forEach(route=>{const routeScenarios=byRoute.get(route.route_id)||[];const startY=currentY;routeScenarios.forEach((scenario,index)=>{scenarioPositions.set(scenario.node_id,{x:720,y:currentY+index*84,left:138,right:138});});const centerY=routeScenarios.length?startY+(routeScenarios.length-1)*42:currentY;routePositions.set(route.route_id,{x:400,y:centerY,left:126,right:126});currentY+=Math.max(1,routeScenarios.length)*84+42;});routes.forEach(route=>{const routeNode=nodeById(route.route_id);const routePosition=routePositions.get(route.route_id);arrow(svg,rootPosition,routePosition,"tree-arrow");(byRoute.get(route.route_id)||[]).forEach(scenario=>arrow(svg,routePosition,scenarioPositions.get(scenario.node_id),"tree-arrow"));nodeShape(svg,routeNode,routePosition);});nodeShape(svg,root,rootPosition);scenarios.forEach(scenario=>nodeShape(svg,scenario,scenarioPositions.get(scenario.node_id),()=>{activeScenarioId=scenario.scenario_id;document.getElementById("scenario-selector").value=activeScenarioId;renderAll();}));const selected=selectedScenarioNode();const downstream=downstreamNodes(selected);if(selected&&scenarioPositions.has(selected.node_id)){const selectedPosition=scenarioPositions.get(selected.node_id);let previousPosition=selectedPosition;downstream.forEach((node,index)=>{const position={x:1000+index*250,y:selectedPosition.y,left:112,right:112};arrow(svg,previousPosition,position,"tree-arrow");nodeShape(svg,node,position);previousPosition=position;});}container.appendChild(svg); }
function renderImage() { const container=document.getElementById("diagram-scroll");container.replaceChildren();document.getElementById("diagram-title").textContent="Original After-Sales Source Image";const source=activeFunction?.source_references?.[activeSourceIndex];if(!source){const empty=document.createElement("p");empty.className="empty-state";empty.textContent="No supplied after-sales source image is mapped to this service function.";container.appendChild(empty);return;}const image=document.createElement("img");image.className="source-image";image.src=source.source_asset||"";image.alt=`${source.tree_name_en||source.tree_id} original after-sales source image`;container.appendChild(image); }
function selectedCase() { return activeTree.scenario_cases.find(item=>item.scenario_id===activeScenarioId)||null; }
function detailItem(label,value) { const item=document.createElement("div");const term=document.createElement("dt");const definition=document.createElement("dd");term.textContent=label;definition.textContent=text(value)||"Not recorded";item.append(term,definition);return item; }
function renderDetail() { const root=document.getElementById("detail-panel");root.replaceChildren();const item=selectedCase();if(!item){root.append(detailItem("Selected scenario","All visible scenarios"));return;}const branch=item.primary_branch||{};const source=branch.source_reference||{};root.append(detailItem("Customer symptom",item.customer_symptom),detailItem("Trigger condition",item.trigger_condition),detailItem("Observed behaviour",item.observed_behaviour),detailItem("Expected behaviour",item.expected_behaviour),detailItem("Primary basis",branch.basis),detailItem("Primary status",branch.status),detailItem("Source route",source.source_path),detailItem("Alternate diagnostic routes",item.alternate_branches.length),detailItem("Representative ticket",item.representative_ticket_id)); }
function renderStageRows() { const body=document.getElementById("stage-rows");body.replaceChildren();const scenario=selectedScenarioNode();if(!scenario)return;const nodes=[scenario,...downstreamNodes(scenario)];nodes.forEach(node=>{const row=document.createElement("tr");[node.node_type,node.label,node.basis,node.status].forEach(value=>{const cell=document.createElement("td");cell.textContent=text(value);row.appendChild(cell);});body.appendChild(row);}); }
function renderScenarioRows() { const body=document.getElementById("scenario-rows");body.replaceChildren();const routesById=new Map(activeTree.routes.map(route=>[route.route_id,route]));const routeIds=new Set(visibleRoutes().map(route=>route.route_id));activeTree.scenario_cases.filter(item=>routeIds.has(item.route_id)).forEach(item=>{const row=document.createElement("tr");if(item.scenario_id===activeScenarioId)row.className="selected";const route=routesById.get(item.route_id);[route?.label,item.scenario_id,item.customer_symptom,item.trigger_condition,item.primary_branch?.basis,item.source_ticket_count,item.alternate_branches.length].forEach((value,index)=>{const cell=document.createElement("td");cell.textContent=text(value);if(index===5)cell.className="count";row.appendChild(cell);});row.addEventListener("click",()=>{activeScenarioId=item.scenario_id;document.getElementById("scenario-selector").value=activeScenarioId;activeView="unified";renderAll();});body.appendChild(row);}); }
function populateSourceSelector() { const selector=document.getElementById("source-selector");selector.replaceChildren();(activeFunction?.source_references||[]).forEach((source,index)=>{const option=document.createElement("option");option.value=String(index);option.textContent=`${source.reference_tree_id||source.tree_id} | ${source.tree_name_en||source.tree_id}`;selector.appendChild(option);});selector.disabled=activeView==="unified"||!(activeFunction?.source_references||[]).length;if(selector.options.length)selector.value=String(activeSourceIndex); }
function populateRouteSelector() { const selector=document.getElementById("route-selector");selector.replaceChildren();const all=document.createElement("option");all.value="";all.textContent=`All complaint routes (${activeTree.scenario_count} scenarios)`;selector.appendChild(all);activeTree.routes.forEach(route=>{const option=document.createElement("option");option.value=route.route_id;option.textContent=`${route.label} (${route.scenario_count} scenarios / ${route.ticket_count} tickets)`;selector.appendChild(option);});if(Array.from(selector.options).some(option=>option.value===activeRouteId))selector.value=activeRouteId;else{activeRouteId="";selector.value="";} }
function populateScenarioSelector() { const selector=document.getElementById("scenario-selector");selector.replaceChildren();const routeIds=new Set(visibleRoutes().map(route=>route.route_id));const cases=activeTree.scenario_cases.filter(item=>routeIds.has(item.route_id));if(!cases.length){const option=document.createElement("option");option.value="";option.textContent="No V12 field scenario";selector.appendChild(option);selector.disabled=true;activeScenarioId="";return;}selector.disabled=false;const allOption=document.createElement("option");allOption.value="";allOption.textContent=`All failure scenarios (${cases.length} scenarios)`;selector.appendChild(allOption);cases.forEach(item=>{const option=document.createElement("option");option.value=item.scenario_id;option.textContent=`${item.scenario_id} | ${text(item.customer_symptom).slice(0,92)} (${item.source_ticket_count} tickets)`;selector.appendChild(option);});if(!cases.some(item=>item.scenario_id===activeScenarioId))activeScenarioId="";selector.value=activeScenarioId; }
function renderMeta() { const visible=scenarioNodesForRoutes(visibleRoutes());document.getElementById("meta-function").textContent=activeFunction?.service_function_label||"";document.getElementById("meta-routes").textContent=String(activeTree?.routes.length||0);document.getElementById("meta-scenarios").textContent=String(activeTree?.scenario_count||0);document.getElementById("meta-tickets").textContent=String(activeTree?.ticket_count||0);document.getElementById("meta-visible").textContent=String(visible.length);document.getElementById("meta-selected").textContent=activeScenarioId||"All visible scenarios";document.getElementById("governance-note").textContent=activeTree?.relationship_policy||""; }
function renderAll() { renderMeta();populateSourceSelector();if(activeView==="unified")renderUnifiedTree();else renderImage();renderDetail();renderScenarioRows();renderStageRows();document.querySelectorAll(".tab").forEach(tab=>tab.setAttribute("aria-selected",String(tab.dataset.view===activeView))); }
function populateFunctionSelector(filterText="") { const selector=document.getElementById("function-selector");const selectedId=activeFunction?.service_function_id||"";const filter=filterText.trim().toLocaleLowerCase();selector.replaceChildren();catalog.functions.filter(item=>!filter||[item.service_function_id,item.service_function_label,item.service_domain].join(" ").toLocaleLowerCase().includes(filter)).forEach(item=>{const option=document.createElement("option");option.value=item.service_function_id;option.textContent=`${item.service_function_label} | ${item.unified_tree.scenario_count} scenarios / ${item.unified_tree.ticket_count} tickets`;selector.appendChild(option);});if(!selector.options.length){const option=document.createElement("option");option.value="";option.textContent="No matching service function";selector.appendChild(option);return;}selector.value=Array.from(selector.options).some(option=>option.value===selectedId)?selectedId:selector.options[0].value; }
function selectFunction(serviceFunctionId) { activeFunction=catalog.functions.find(item=>item.service_function_id===serviceFunctionId)||catalog.functions[0]||null;activeTree=activeFunction?.unified_tree||null;activeRouteId="";activeSourceIndex=0;activeScenarioId="";if(activeTree){populateRouteSelector();populateScenarioSelector();}renderAll(); }
function initializeSummary() { const summary=catalog.summary||{};document.getElementById("summary-functions").textContent=text(summary.service_function_count);document.getElementById("summary-trees").textContent=text(summary.service_tree_count);document.getElementById("summary-scenarios").textContent=text(summary.scenario_count);document.getElementById("summary-tickets").textContent=text(summary.field_ticket_count);document.getElementById("summary-source-routes").textContent=text(summary.after_sales_route_count);document.getElementById("summary-field-routes").textContent=text(summary.field_failure_route_count);document.getElementById("summary-nodes").textContent=text(summary.tree_node_count); }
document.getElementById("function-filter").addEventListener("input",event=>{populateFunctionSelector(event.target.value);selectFunction(document.getElementById("function-selector").value);});
document.getElementById("function-selector").addEventListener("change",event=>selectFunction(event.target.value));
document.getElementById("route-selector").addEventListener("change",event=>{activeRouteId=event.target.value;populateScenarioSelector();renderAll();});
document.getElementById("scenario-selector").addEventListener("change",event=>{activeScenarioId=event.target.value;renderAll();});
document.getElementById("source-selector").addEventListener("change",event=>{activeSourceIndex=Number(event.target.value||0);renderAll();});
document.querySelectorAll(".tab").forEach(tab=>tab.addEventListener("click",()=>{activeView=tab.dataset.view;renderAll();}));
initializeSummary();populateFunctionSelector();selectFunction(document.getElementById("function-selector").value);
</script>
</body>
</html>
"""
    return template.replace("__CATALOG_DATA__", serialized_catalog)


def write_service_function_tree_catalog_artifacts(
    *, catalog: dict[str, Any], output_html: Path, output_json: Path
) -> None:
    """Write standalone V3 HTML and JSON artifacts."""
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    output_html.write_text(
        render_service_function_tree_catalog_html(catalog), encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build unified per-service-function Phase 09 diagnosis trees."
    )
    parser.add_argument("--scenario-workbook", type=Path, default=DEFAULT_SCENARIO_WORKBOOK)
    parser.add_argument("--knowledge-base", type=Path, default=DEFAULT_KNOWLEDGE_BASE)
    parser.add_argument("--source-workbook", type=Path, default=DEFAULT_SOURCE_WORKBOOK)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--extracted-directory", type=Path, default=DEFAULT_EXTRACTED_DIRECTORY)
    parser.add_argument("--output-html", type=Path, default=DEFAULT_OUTPUT_HTML)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    args = parser.parse_args(argv)
    catalog = build_service_function_tree_catalog_from_files(
        scenario_workbook_path=args.scenario_workbook,
        knowledge_base_path=args.knowledge_base,
        source_workbook_path=args.source_workbook,
        registry_path=args.registry,
        manifest_path=args.manifest,
        extracted_directory=args.extracted_directory,
    )
    write_service_function_tree_catalog_artifacts(
        catalog=catalog,
        output_html=args.output_html,
        output_json=args.output_json,
    )
    print(f"Saved HTML: {args.output_html}")
    print(f"Saved JSON: {args.output_json}")
    print(f"Service trees: {catalog['summary']['service_tree_count']}")
    print(f"Scenarios in unified trees: {catalog['summary']['scenario_count']}")
    print(f"After-sales complaint routes: {catalog['summary']['after_sales_route_count']}")
    print(f"Field-failure routes: {catalog['summary']['field_failure_route_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())