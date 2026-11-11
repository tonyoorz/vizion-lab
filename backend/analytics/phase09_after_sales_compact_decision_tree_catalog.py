#!/usr/bin/env python3
"""Build compact after-sales decision trees from source routes and evidence.

V5 intentionally matches the density of manual after-sales trees: source-backed
flows are compressed to complaint routes and diagnostic resolution leaves;
functions without a source tree use one compact check with Yes/No outcomes.
Historical scenarios remain support evidence, not structural tree nodes.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
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
    / "IDCEVO_Phase09_All_Function_AfterSales_Compact_Decision_Tree_Catalog_V5.html"
)
DEFAULT_OUTPUT_JSON = (
    PHASE09_EXPORT_DIR
    / "IDCEVO_Phase09_All_Function_AfterSales_Compact_Decision_Tree_Catalog_V5.json"
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
    ticket_evidence = branch.get("ticket_evidence")
    if not isinstance(ticket_evidence, dict):
        ticket_evidence = {}
    return (
        _BRANCH_PRIORITY.get(_text(branch.get("basis")), 99),
        -float(branch.get("match_score", 0) or 0),
        -float(ticket_evidence.get("support_ratio", 0) or 0),
        _text(branch.get("branch_id")),
    )


def _primary_branch(path: dict[str, Any]) -> dict[str, Any]:
    branches = [
        branch
        for branch in path.get("diagnostic_branches", [])
        if isinstance(branch, dict)
    ]
    if not branches:
        return {
            "basis": "evidence_collection_template",
            "status": "no_verified_causal_evidence",
            "possible_cause": "No verified technical cause recorded",
            "check_steps": "Collect evidence before further diagnosis.",
            "resolution": "",
            "resolution_status": "no_recorded_recovery",
            "escalation": "Prepare an engineering evidence package",
            "source_reference": {},
        }
    return sorted(branches, key=_branch_sort_key)[0]


def _new_node(
    *,
    node_id: str,
    node_type: str,
    label: str,
    question: str = "",
    possible_cause: str = "",
    check_steps: str = "",
    resolution: str = "",
    escalation: str = "",
    basis: str = "",
    status: str = "",
    yes_next_node: str = "",
    no_next_node: str = "",
    source_tree_id: str = "",
    source_node_id: str = "",
    source_path: str = "",
) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "node_type": node_type,
        "label": label,
        "question": question,
        "possible_cause": possible_cause,
        "check_steps": check_steps,
        "resolution": resolution,
        "escalation": escalation,
        "basis": basis,
        "status": status,
        "yes_next_node": yes_next_node,
        "no_next_node": no_next_node,
        "source_tree_id": source_tree_id,
        "source_node_id": source_node_id,
        "source_path": source_path,
        "source_scenario_ids": [],
        "source_scenario_count": 0,
        "source_ticket_count": 0,
    }


def _add_edge(
    edges: list[dict[str, str]], from_node_id: str, to_node_id: str, outcome: str
) -> None:
    if not from_node_id or not to_node_id:
        return
    edges.append({
        "edge_id": _stable_id("EDGE", from_node_id, to_node_id, outcome),
        "from_node_id": from_node_id,
        "to_node_id": to_node_id,
        "outcome": outcome,
    })


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
        })
    return sorted(
        references,
        key=lambda reference: (reference["tree_id"], reference["reference_tree_id"]),
    )


def _compact_evidence(path: dict[str, Any], assigned_node_id: str, basis: str) -> dict[str, Any]:
    return {
        "scenario_id": _text(path.get("scenario_id")),
        "customer_symptom": _text(path.get("customer_symptom")),
        "trigger_condition": _text(path.get("trigger_condition")),
        "observed_behaviour": _text(path.get("observed_behaviour")),
        "expected_behaviour": _text(path.get("expected_behaviour")),
        "problem_type": _text(path.get("problem_type")),
        "representative_ticket_id": _text(path.get("representative_ticket_id")),
        "source_ticket_count": _integer(path.get("source_ticket_count")),
        "evidence_confidence": _text(path.get("evidence_confidence")),
        "assigned_node_id": assigned_node_id,
        "assignment_basis": basis,
    }


def _field_check_question(issue_label: str) -> str:
    normalized = issue_label.casefold()
    if any(term in normalized for term in ("authentication", "account", "activation")):
        check = "Are account, entitlement, activation, market, and vehicle binding conditions verified?"
    elif any(term in normalized for term in ("connection", "pairing")):
        check = "Are compatibility, pairing, and transport-link conditions verified?"
    elif any(term in normalized for term in ("navigation", "location")):
        check = "Are destination or POI input, location context, account state, and connectivity verified?"
    elif any(term in normalized for term in ("charging", "energy")):
        check = "Are vehicle, charging, account, and service eligibility conditions verified?"
    elif any(term in normalized for term in ("update", "programming")):
        check = "Are power, network, storage, package, and vehicle-state update conditions verified?"
    elif any(term in normalized for term in ("audio", "media")):
        check = "Are source selection, audio route, volume, and focus conditions verified?"
    elif any(term in normalized for term in ("display", "projection")):
        check = "Are display mode, application state, and vehicle-display conditions verified?"
    else:
        check = "Are the documented feature prerequisites and operating conditions verified?"
    return f"For {issue_label}: {check}"


def _recovery_summary(paths: list[dict[str, Any]]) -> str:
    actions: Counter[str] = Counter()
    for path in paths:
        primary = _primary_branch(path)
        action = _text(primary.get("resolution"))
        if action and _text(primary.get("resolution_status")) == "ticket_recorded_workaround_not_approved":
            actions[action] += _integer(path.get("source_ticket_count"))
    if not actions:
        return ""
    visible = actions.most_common(2)
    text = "; ".join(f"{action} ({count} tickets)" for action, count in visible)
    if len(actions) > len(visible):
        text += f"; Other recorded candidates ({len(actions) - len(visible)})"
    return text


def _route_label_from_leaf(leaf: dict[str, Any]) -> str:
    segments = [
        segment.strip()
        for segment in _text(leaf.get("source_path")).split(" > ")
        if segment.strip()
    ]
    return segments[1] if len(segments) > 1 else _text(leaf.get("source_text"))


def build_compact_after_sales_tree(function: dict[str, Any]) -> dict[str, Any]:
    """Create one compact manual-tree-density flow for a service function."""
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
    source_graphs = [
        graph for graph in function.get("source_graphs", []) if isinstance(graph, dict)
    ]
    source_graph_by_tree_id = {
        _text(graph.get("tree_id")): graph for graph in source_graphs
    }
    assignments_by_leaf: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    field_paths_by_issue: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for path in scenario_paths:
        primary = _primary_branch(path)
        source_reference = primary.get("source_reference")
        if not isinstance(source_reference, dict):
            source_reference = {}
        source_tree_id = _text(source_reference.get("source_tree_id"))
        source_node_id = _text(source_reference.get("source_node_id"))
        graph = source_graph_by_tree_id.get(source_tree_id)
        graph_node_ids = {
            _text(node.get("node_id"))
            for node in graph.get("nodes", [])
            if isinstance(node, dict)
        } if graph else set()
        if (
            _text(primary.get("basis")) == "after_sales_experience_source_path"
            and graph
            and source_node_id in graph_node_ids
        ):
            assignments_by_leaf[(source_tree_id, source_node_id)].append(path)
        else:
            issue_label = _text(path.get("problem_type")) or "Other observed field failure"
            field_paths_by_issue[issue_label].append(path)

    nodes_by_id: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, str]] = []
    root_id = "ROOT"
    nodes_by_id[root_id] = _new_node(
        node_id=root_id,
        node_type="Entry",
        label=service_label,
        question="Customer complaint: select the matching after-sales route.",
        basis="service_function_mapping",
        status="compact_after_sales_tree_root",
    )

    scenario_evidence: list[dict[str, Any]] = []
    route_nodes: dict[tuple[str, str], str] = {}
    for graph in sorted(source_graphs, key=lambda item: _text(item.get("tree_id"))):
        graph_tree_id = _text(graph.get("tree_id"))
        leaf_nodes = [
            node
            for node in graph.get("nodes", [])
            if isinstance(node, dict) and bool(node.get("is_terminal"))
        ]
        by_route: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for leaf in leaf_nodes:
            by_route[_route_label_from_leaf(leaf)].append(leaf)
        for route_label, leaves in sorted(by_route.items()):
            route_key = (graph_tree_id, route_label)
            route_id = f"ROUTE-{service_function_id}-{graph_tree_id}-{_stable_id('', route_label).lstrip('-')}"
            route_nodes[route_key] = route_id
            nodes_by_id[route_id] = _new_node(
                node_id=route_id,
                node_type="Source Complaint Route",
                label=route_label,
                basis="after_sales_source_tree",
                status="source_tree_complaint_route",
                source_tree_id=graph_tree_id,
                source_path=route_label,
            )
            _add_edge(edges, root_id, route_id, "MATCH")
            route_paths: list[dict[str, Any]] = []
            for leaf in sorted(leaves, key=lambda item: _text(item.get("source_path"))):
                source_node_id = _text(leaf.get("node_id"))
                diagnosis_id = f"DIAG-{service_function_id}-{source_node_id}"
                paths = assignments_by_leaf.get((graph_tree_id, source_node_id), [])
                route_paths.extend(paths)
                nodes_by_id[diagnosis_id] = _new_node(
                    node_id=diagnosis_id,
                    node_type="Source Diagnosis",
                    label=_text(leaf.get("source_text")),
                    possible_cause=_text(leaf.get("possible_cause")),
                    check_steps=_text(leaf.get("check_step")),
                    resolution=_text(leaf.get("solution")),
                    escalation=_text(leaf.get("escalation_path")),
                    basis="after_sales_source_tree",
                    status="source_tree_diagnosis_and_resolution",
                    source_tree_id=graph_tree_id,
                    source_node_id=source_node_id,
                    source_path=_text(leaf.get("source_path")),
                )
                nodes_by_id[diagnosis_id]["source_scenario_ids"] = sorted(
                    _text(path.get("scenario_id")) for path in paths
                )
                nodes_by_id[diagnosis_id]["source_scenario_count"] = len(paths)
                nodes_by_id[diagnosis_id]["source_ticket_count"] = sum(
                    _integer(path.get("source_ticket_count")) for path in paths
                )
                _add_edge(edges, route_id, diagnosis_id, "CHECK")
                for path in paths:
                    scenario_evidence.append(_compact_evidence(
                        path, diagnosis_id, "after_sales_source_leaf_match"
                    ))
            nodes_by_id[route_id]["source_scenario_ids"] = sorted({
                _text(path.get("scenario_id")) for path in route_paths
            })
            nodes_by_id[route_id]["source_scenario_count"] = len(
                nodes_by_id[route_id]["source_scenario_ids"]
            )
            nodes_by_id[route_id]["source_ticket_count"] = sum(
                _integer(path.get("source_ticket_count")) for path in route_paths
            )

    for issue_label, paths in sorted(
        field_paths_by_issue.items(),
        key=lambda item: (
            -sum(_integer(path.get("source_ticket_count")) for path in item[1]),
            item[0],
        ),
    ):
        suffix = _stable_id("", service_function_id, issue_label).lstrip("-")
        route_id = f"FIELD-{service_function_id}-{suffix}"
        check_id = f"CHECK-{service_function_id}-{suffix}"
        yes_outcome_id = f"OUT-YES-{service_function_id}-{suffix}"
        no_outcome_id = f"OUT-NO-{service_function_id}-{suffix}"
        scenario_ids = sorted(_text(path.get("scenario_id")) for path in paths)
        ticket_count = sum(_integer(path.get("source_ticket_count")) for path in paths)
        recovery = _recovery_summary(paths)
        nodes_by_id[route_id] = _new_node(
            node_id=route_id,
            node_type="Field Complaint Route",
            label=issue_label,
            basis="historical_field_issue_route",
            status="field_evidence_route",
        )
        nodes_by_id[check_id] = _new_node(
            node_id=check_id,
            node_type="Compact Check",
            label="Verify key conditions",
            question=_field_check_question(issue_label),
            check_steps=(
                "Verify the current condition, reproduce the customer trigger, and capture "
                "the minimum ECU/software/log evidence needed for this route."
            ),
            basis="compact_evidence_gate_template",
            status="requires_current_case_verification",
            yes_next_node=yes_outcome_id,
            no_next_node=no_outcome_id,
        )
        nodes_by_id[yes_outcome_id] = _new_node(
            node_id=yes_outcome_id,
            node_type="Field Outcome",
            label=(
                recovery
                or "Collect technical evidence and route the verified case to engineering."
            ),
            resolution=recovery,
            escalation=(
                "Ticket-recorded recovery remains unapproved; verify current-case fit or "
                "prepare engineering escalation."
                if recovery
                else "Prepare an engineering evidence package after the compact check."
            ),
            basis=(
                "ticket_recorded_recovery"
                if recovery
                else "compact_evidence_gate_template"
            ),
            status=(
                "not_an_approved_repair" if recovery else "engineering_escalation_required"
            ),
        )
        nodes_by_id[no_outcome_id] = _new_node(
            node_id=no_outcome_id,
            node_type="Field Outcome",
            label="Correct missing conditions, capture evidence, and retest.",
            check_steps=(
                "Correct prerequisites or context gaps, record the result, and retry the "
                "same customer trigger before escalation."
            ),
            escalation="If the issue persists, prepare an engineering evidence package.",
            basis="compact_evidence_gate_template",
            status="correct_and_retest",
        )
        for node_id in (route_id, check_id, yes_outcome_id, no_outcome_id):
            node = nodes_by_id[node_id]
            node["source_scenario_ids"] = list(scenario_ids)
            node["source_scenario_count"] = len(scenario_ids)
            node["source_ticket_count"] = ticket_count
        _add_edge(edges, root_id, route_id, "MATCH")
        _add_edge(edges, route_id, check_id, "CHECK")
        _add_edge(edges, check_id, yes_outcome_id, "YES")
        _add_edge(edges, check_id, no_outcome_id, "NO")
        for path in paths:
            scenario_evidence.append(_compact_evidence(
                path, route_id, "field_issue_evidence_route"
            ))

    edges_by_id = {edge["edge_id"]: edge for edge in edges}
    nodes = sorted(nodes_by_id.values(), key=lambda node: node["node_id"])
    return {
        "tree_id": f"DTC-{service_function_id}",
        "service_function_id": service_function_id,
        "service_function_label": service_label,
        "service_domain": _text(function.get("service_domain")),
        "scenario_count": len(scenario_evidence),
        "ticket_count": sum(
            _integer(evidence["source_ticket_count"]) for evidence in scenario_evidence
        ),
        "nodes": nodes,
        "edges": sorted(edges_by_id.values(), key=lambda edge: edge["edge_id"]),
        "scenario_evidence": sorted(
            scenario_evidence,
            key=lambda evidence: (-evidence["source_ticket_count"], evidence["scenario_id"]),
        ),
        "source_graph_count": len(source_graphs),
        "tree_generation": "after_sales_compact_decision_tree_v5",
        "relationship_policy": (
            "Compact routes and terminal diagnoses form the tree structure. Historical "
            "scenarios are evidence attached to a route or diagnosis leaf, not a "
            "causal or chronological tree progression."
        ),
    }


def build_compact_decision_tree_catalog_from_scenario_catalog(
    scenario_catalog: dict[str, Any],
) -> dict[str, Any]:
    """Convert V2 scenario evidence into one compact tree per service function."""
    functions = []
    for function in scenario_catalog.get("functions", []):
        if not isinstance(function, dict):
            continue
        compact_tree = build_compact_after_sales_tree(function)
        functions.append({
            "service_function_id": _text(function.get("service_function_id")),
            "service_function": _text(function.get("service_function")),
            "service_function_label": _text(function.get("service_function_label")),
            "service_domain": _text(function.get("service_domain")),
            "catalog_status": _text(function.get("catalog_status")),
            "source_references": _source_references(function),
            "compact_tree": compact_tree,
        })
    functions.sort(
        key=lambda function: (
            function["service_domain"].casefold(),
            function["service_function_label"].casefold(),
        )
    )
    trees = [function["compact_tree"] for function in functions]
    scenario_evidence = [
        evidence for tree in trees for evidence in tree["scenario_evidence"]
    ]
    return {
        "schema_version": "phase09-after-sales-compact-decision-tree-v5",
        "data_policy": {
            "source_tree_policy": (
                "Source-backed functions preserve the manual-tree density of complaint route "
                "to diagnostic leaf, with causes, checks, resolutions, and escalation held "
                "inside the terminal diagnosis detail."
            ),
            "historical_evidence_policy": (
                "Historical scenarios support compact routes and leaves; they do not create "
                "a category node or a causal progression in the tree."
            ),
            "no_source_policy": (
                "Functions without a source tree use one compact current-case check with "
                "Yes/No outcomes, not invented function-specific repair instructions."
            ),
        },
        "summary": {
            "service_function_count": len(functions),
            "compact_tree_count": len(trees),
            "scenario_evidence_count": len(scenario_evidence),
            "field_ticket_count": sum(
                _integer(evidence["source_ticket_count"])
                for evidence in scenario_evidence
            ),
            "compact_node_count": sum(len(tree["nodes"]) for tree in trees),
            "after_sales_source_flow_count": sum(
                tree["source_graph_count"] for tree in trees
            ),
            "source_leaf_evidence_count": sum(
                evidence["assignment_basis"] == "after_sales_source_leaf_match"
                for evidence in scenario_evidence
            ),
            "field_route_evidence_count": sum(
                evidence["assignment_basis"] == "field_issue_evidence_route"
                for evidence in scenario_evidence
            ),
            "source_reference_count": sum(
                len(function["source_references"]) for function in functions
            ),
        },
        "functions": functions,
    }


def build_compact_decision_tree_catalog_from_files(
    *,
    scenario_workbook_path: Path,
    knowledge_base_path: Path,
    source_workbook_path: Path,
    registry_path: Path,
    manifest_path: Path,
    extracted_directory: Path,
) -> dict[str, Any]:
    """Build V5 from current scenario, ticket, and source-tree inputs."""
    scenario_catalog = build_scenario_diagnosis_catalog_from_files(
        scenario_workbook_path=scenario_workbook_path,
        knowledge_base_path=knowledge_base_path,
        source_workbook_path=source_workbook_path,
        registry_path=registry_path,
        manifest_path=manifest_path,
        extracted_directory=extracted_directory,
    )
    return build_compact_decision_tree_catalog_from_scenario_catalog(scenario_catalog)


def _serialized_html_data(value: Any) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def render_compact_after_sales_decision_tree_catalog_html(catalog: dict[str, Any]) -> str:
    """Render compact manual-density decision trees with historical evidence tables."""
    serialized_catalog = _serialized_html_data(catalog)
    template = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>All-Function Compact After-Sales Decision Tree Catalog</title>
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
.controls { display:grid; grid-template-columns:minmax(250px,.7fr) minmax(380px,1.3fr); gap:14px; margin:20px 0 14px; }
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
.diagram-scroll { min-width:0; overflow:auto; min-height:460px; max-height:760px; }
svg { display:block; min-width:1420px; width:100%; height:auto; }
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
    <h1>All-Function Compact After-Sales Decision Tree Catalog</h1>
    <p class="subtitle">Manual after-sales tree density: complaint route -> diagnosis and check -> solution or escalation. Historical scenarios support a route or diagnosis leaf instead of becoming the tree structure.</p>
  </header>
  <section class="summary" aria-label="Catalog summary">
    <div><span>Service functions</span><strong id="summary-functions"></strong></div>
    <div><span>Compact trees</span><strong id="summary-trees"></strong></div>
    <div><span>Historical scenarios</span><strong id="summary-scenarios"></strong></div>
    <div><span>Field tickets</span><strong id="summary-tickets"></strong></div>
    <div><span>Source flows</span><strong id="summary-source-flows"></strong></div>
    <div><span>Source-leaf evidence</span><strong id="summary-source-evidence"></strong></div>
    <div><span>Field-route evidence</span><strong id="summary-field-evidence"></strong></div>
  </section>
  <section class="controls">
    <label for="function-filter">Find a function<input id="function-filter" type="search" placeholder="Function, domain, or ID"></label>
    <label for="function-selector">Service function<select id="function-selector"></select></label>
  </section>
  <section class="tree-meta">
    <div><span>Function</span><strong id="meta-function"></strong></div>
    <div><span>Compact nodes</span><strong id="meta-nodes"></strong></div>
    <div><span>Historical scenarios</span><strong id="meta-scenarios"></strong></div>
    <div><span>Historical tickets</span><strong id="meta-tickets"></strong></div>
    <div><span>Source flow coverage</span><strong id="meta-source"></strong></div>
    <div><span>Selected node</span><strong id="meta-selected"></strong></div>
  </section>
  <p class="governance" id="governance-note"></p>
  <nav class="tabs" role="tablist" aria-label="Tree views">
    <button class="tab" type="button" role="tab" data-view="tree" aria-selected="true">Compact After-Sales Decision Tree</button>
    <button class="tab" type="button" role="tab" data-view="image" aria-selected="false">Original After-Sales Source Image</button>
  </nav>
  <section class="workspace">
    <section class="diagram-panel">
      <header class="panel-head"><span id="diagram-title"></span><select id="source-selector" aria-label="After-sales source reference"></select></header>
      <div class="diagram-scroll" id="diagram-scroll"></div>
    </section>
    <aside class="detail-panel"><header class="panel-head">Selected route or diagnosis</header><dl id="detail-panel"></dl></aside>
  </section>
  <h2 class="section-title">Historical Scenario Evidence</h2>
  <label for="evidence-filter">Filter evidence<input id="evidence-filter" type="search" placeholder="Scenario, symptom, trigger, or route"></label>
  <section class="table-shell"><table><thead><tr><th>Assigned route / diagnosis</th><th>Assignment basis</th><th>Scenario</th><th>Customer symptom</th><th>Trigger</th><th>Tickets</th><th>Confidence</th></tr></thead><tbody id="evidence-rows"></tbody></table></section>
</main>
<script id="catalog-data" type="application/json">__CATALOG_DATA__</script>
<script>
const catalog = JSON.parse(document.getElementById("catalog-data").textContent);
const NS = "http://www.w3.org/2000/svg";
const colors = { Entry:"#d7ead1", "Source Complaint Route":"#b8d7a9", "Source Diagnosis":"#e2ece8", "Field Complaint Route":"#d8e8f5", "Compact Check":"#fff0c8", "Field Outcome":"#f7d4d4" };
let activeFunction = null;
let activeTree = null;
let activeView = "tree";
let activeSourceIndex = 0;
let activeNodeId = "";
function text(value) { return String(value || ""); }
function svgElement(name, attributes = {}) { const element=document.createElementNS(NS,name);Object.entries(attributes).forEach(([key,value])=>element.setAttribute(key,text(value)));return element; }
function wrapLines(value,limit) { const chars=Array.from(text(value));const lines=[];while(chars.length&&lines.length<3)lines.push(chars.splice(0,limit).join(""));return lines.length?lines:[""]; }
function drawText(svg,value,x,y,limit,color="#17242c",fontSize=12) { const lines=wrapLines(value,limit);const label=svgElement("text",{x,y:y-(lines.length-1)*8,"text-anchor":"middle","font-size":fontSize,fill:color});lines.forEach((line,index)=>{const span=svgElement("tspan",{x,dy:index?15:0});span.textContent=line;label.appendChild(span);});svg.appendChild(label);return label; }
function drawEdge(svg,from,to,outcome,markerId) { svg.appendChild(svgElement("line",{x1:from.x+from.right,y1:from.y,x2:to.x-to.left,y2:to.y,stroke:"#536670","stroke-width":1.3,"marker-end":`url(#${markerId})`}));if(outcome){const label=drawText(svg,outcome,(from.x+from.right+to.x-to.left)/2,(from.y+to.y)/2-6,9,"#435862",10);label.setAttribute("class","edge-label");} }
function nodeShape(svg,node,position) { const isCheck=node.node_type==="Compact Check";const group=svgElement("g",{tabindex:0,role:"button",cursor:"pointer","aria-label":`${node.node_type}: ${node.question||node.label}`});const title=svgElement("title");title.textContent=`${node.node_type}: ${node.question||node.label}`;group.appendChild(title);if(isCheck){group.appendChild(svgElement("polygon",{points:`${position.x},${position.y-50} ${position.x+position.left},${position.y} ${position.x},${position.y+50} ${position.x-position.left},${position.y}`,fill:colors[node.node_type]||"#eceff1",stroke:node.node_id===activeNodeId?"#b55a19":"#59717c","stroke-width":node.node_id===activeNodeId?3:1.2}));}else{group.appendChild(svgElement("rect",{x:position.x-position.left,y:position.y-42,width:position.left*2,height:84,rx:4,fill:colors[node.node_type]||"#eceff1",stroke:node.node_id===activeNodeId?"#b55a19":"#59717c","stroke-width":node.node_id===activeNodeId?3:1.2}));}svg.appendChild(group);drawText(svg,node.node_type,position.x,position.y-20,22,"#52616b",10);drawText(svg,node.question||node.label,position.x,position.y+5,isCheck?22:28);if(node.source_ticket_count)drawText(svg,`${node.source_scenario_count} scenarios / ${node.source_ticket_count} tickets`,position.x,position.y+29,25,"#52616b",10);group.addEventListener("click",()=>{activeNodeId=node.node_id;renderAll();});group.addEventListener("keydown",event=>{if(event.key==="Enter"||event.key===" "){event.preventDefault();activeNodeId=node.node_id;renderAll();}}); }
function nodeById(nodeId) { return activeTree.nodes.find(node=>node.node_id===nodeId)||null; }
function activeSource() { return activeFunction?.source_references?.[activeSourceIndex]||null; }
function renderTree() { const container=document.getElementById("diagram-scroll");container.replaceChildren();document.getElementById("diagram-title").textContent="Compact After-Sales Decision Tree";if(!activeTree)return;const nodes=activeTree.nodes;const byId=new Map(nodes.map(node=>[node.node_id,node]));const outgoing=new Map();nodes.forEach(node=>outgoing.set(node.node_id,[]));activeTree.edges.forEach(edge=>{if(outgoing.has(edge.from_node_id)&&byId.has(edge.to_node_id))outgoing.get(edge.from_node_id).push(edge);});const depth=new Map([["ROOT",0]]);const queue=["ROOT"];for(let index=0;index<queue.length;index+=1){const nodeId=queue[index];(outgoing.get(nodeId)||[]).forEach(edge=>{if(!depth.has(edge.to_node_id)){depth.set(edge.to_node_id,(depth.get(nodeId)||0)+1);queue.push(edge.to_node_id);}});}nodes.forEach(node=>{if(!depth.has(node.node_id))depth.set(node.node_id,0);});const levels=new Map();nodes.forEach(node=>{const level=depth.get(node.node_id)||0;if(!levels.has(level))levels.set(level,[]);levels.get(level).push(node);});const maxDepth=Math.max(0,...levels.keys());const maxRows=Math.max(1,...Array.from(levels.values(),items=>items.length));const width=Math.max(1420,170+(maxDepth+1)*330);const height=Math.max(480,120+maxRows*100);const svg=svgElement("svg",{viewBox:`0 0 ${width} ${height}`,role:"img","aria-label":"Compact after-sales decision tree"});const defs=svgElement("defs");const marker=svgElement("marker",{id:"compact-arrow",markerWidth:8,markerHeight:8,refX:7,refY:4,orient:"auto",markerUnits:"strokeWidth"});marker.appendChild(svgElement("path",{d:"M 0 0 L 8 4 L 0 8 z",fill:"#536670"}));defs.appendChild(marker);svg.appendChild(defs);const positions=new Map();Array.from(levels.entries()).forEach(([level,items])=>{items.sort((left,right)=>left.node_id.localeCompare(right.node_id));const gap=height/(items.length+1);items.forEach((node,index)=>positions.set(node.node_id,{x:140+level*330,y:gap*(index+1),left:135,right:135}));});activeTree.edges.forEach(edge=>{const from=positions.get(edge.from_node_id),to=positions.get(edge.to_node_id);if(from&&to)drawEdge(svg,from,to,edge.outcome,"compact-arrow");});nodes.forEach(node=>nodeShape(svg,node,positions.get(node.node_id)));container.appendChild(svg); }
function renderImage() { const container=document.getElementById("diagram-scroll");container.replaceChildren();document.getElementById("diagram-title").textContent="Original After-Sales Source Image";const source=activeSource();if(!source){const empty=document.createElement("p");empty.className="empty-state";empty.textContent="No supplied after-sales source image is mapped to this service function. The compact tree uses a field route and one compact check.";container.appendChild(empty);return;}const image=document.createElement("img");image.className="source-image";image.src=source.source_asset||"";image.alt=`${source.tree_name_en||source.tree_id} original after-sales source image`;container.appendChild(image); }
function detailItem(label,value) { const item=document.createElement("div");const term=document.createElement("dt");const definition=document.createElement("dd");term.textContent=label;definition.textContent=text(value)||"Not recorded";item.append(term,definition);return item; }
function renderDetail() { const root=document.getElementById("detail-panel");root.replaceChildren();const node=nodeById(activeNodeId)||nodeById("ROOT");if(!node){root.append(detailItem("Tree node","No node selected."));return;}root.append(detailItem("Node type",node.node_type),detailItem("Question",node.question),detailItem("Possible cause",node.possible_cause),detailItem("Check steps",node.check_steps),detailItem("Resolution",node.resolution),detailItem("Escalation",node.escalation),detailItem("Basis",node.basis),detailItem("Status",node.status),detailItem("Source path",node.source_path),detailItem("Supporting scenarios",node.source_scenario_count),detailItem("Supporting tickets",node.source_ticket_count),detailItem("Scenario IDs",(node.source_scenario_ids||[]).join("; "))); }
function renderEvidenceRows() { const body=document.getElementById("evidence-rows");body.replaceChildren();const filter=document.getElementById("evidence-filter").value.trim().toLocaleLowerCase();(activeTree?.scenario_evidence||[]).filter(item=>!filter||Object.values(item).join(" ").toLocaleLowerCase().includes(filter)).forEach(item=>{const row=document.createElement("tr");if(item.assigned_node_id===activeNodeId)row.className="selected";[item.assigned_node_id,item.assignment_basis,item.scenario_id,item.customer_symptom,item.trigger_condition,item.source_ticket_count,item.evidence_confidence].forEach((value,index)=>{const cell=document.createElement("td");cell.textContent=text(value);if(index===5)cell.className="count";row.appendChild(cell);});row.addEventListener("click",()=>{activeNodeId=item.assigned_node_id;activeView="tree";renderAll();});body.appendChild(row);}); }
function populateSourceSelector() { const selector=document.getElementById("source-selector");selector.replaceChildren();(activeFunction?.source_references||[]).forEach((source,index)=>{const option=document.createElement("option");option.value=String(index);option.textContent=`${source.reference_tree_id||source.tree_id} | ${source.tree_name_en||source.tree_id}`;selector.appendChild(option);});selector.disabled=activeView==="tree"||!(activeFunction?.source_references||[]).length;if(selector.options.length)selector.value=String(activeSourceIndex); }
function renderMeta() { document.getElementById("meta-function").textContent=activeFunction?.service_function_label||"";document.getElementById("meta-nodes").textContent=String(activeTree?.nodes.length||0);document.getElementById("meta-scenarios").textContent=String(activeTree?.scenario_count||0);document.getElementById("meta-tickets").textContent=String(activeTree?.ticket_count||0);document.getElementById("meta-source").textContent=(activeFunction?.source_references||[]).length?`${activeFunction.source_references.length} source flow(s)`:"Compact field route";document.getElementById("meta-selected").textContent=activeNodeId||"ROOT";document.getElementById("governance-note").textContent=activeTree?.relationship_policy||""; }
function renderAll() { renderMeta();populateSourceSelector();if(activeView==="tree")renderTree();else renderImage();renderDetail();renderEvidenceRows();document.querySelectorAll(".tab").forEach(tab=>tab.setAttribute("aria-selected",String(tab.dataset.view===activeView))); }
function populateFunctionSelector(filterText="") { const selector=document.getElementById("function-selector");const selectedId=activeFunction?.service_function_id||"";const filter=filterText.trim().toLocaleLowerCase();selector.replaceChildren();catalog.functions.filter(item=>!filter||[item.service_function_id,item.service_function_label,item.service_domain].join(" ").toLocaleLowerCase().includes(filter)).forEach(item=>{const option=document.createElement("option");option.value=item.service_function_id;option.textContent=`${item.service_function_label} | ${item.compact_tree.scenario_count} scenarios / ${item.compact_tree.ticket_count} tickets`;selector.appendChild(option);});if(!selector.options.length){const option=document.createElement("option");option.value="";option.textContent="No matching service function";selector.appendChild(option);return;}selector.value=Array.from(selector.options).some(option=>option.value===selectedId)?selectedId:selector.options[0].value; }
function selectFunction(serviceFunctionId) { activeFunction=catalog.functions.find(item=>item.service_function_id===serviceFunctionId)||catalog.functions[0]||null;activeTree=activeFunction?.compact_tree||null;activeSourceIndex=0;activeNodeId="ROOT";activeView="tree";renderAll(); }
function initializeSummary() { const summary=catalog.summary||{};document.getElementById("summary-functions").textContent=text(summary.service_function_count);document.getElementById("summary-trees").textContent=text(summary.compact_tree_count);document.getElementById("summary-scenarios").textContent=text(summary.scenario_evidence_count);document.getElementById("summary-tickets").textContent=text(summary.field_ticket_count);document.getElementById("summary-source-flows").textContent=text(summary.after_sales_source_flow_count);document.getElementById("summary-source-evidence").textContent=text(summary.source_leaf_evidence_count);document.getElementById("summary-field-evidence").textContent=text(summary.field_route_evidence_count); }
document.getElementById("function-filter").addEventListener("input",event=>{populateFunctionSelector(event.target.value);selectFunction(document.getElementById("function-selector").value);});
document.getElementById("function-selector").addEventListener("change",event=>selectFunction(event.target.value));
document.getElementById("source-selector").addEventListener("change",event=>{activeSourceIndex=Number(event.target.value||0);renderAll();});
document.getElementById("evidence-filter").addEventListener("input",renderEvidenceRows);
document.querySelectorAll(".tab").forEach(tab=>tab.addEventListener("click",()=>{activeView=tab.dataset.view;renderAll();}));
initializeSummary();populateFunctionSelector();selectFunction(document.getElementById("function-selector").value);
</script>
</body>
</html>
"""
    return template.replace("__CATALOG_DATA__", serialized_catalog)


def write_compact_decision_tree_catalog_artifacts(
    *, catalog: dict[str, Any], output_html: Path, output_json: Path
) -> None:
    """Write standalone V5 HTML and JSON artifacts."""
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    output_html.write_text(
        render_compact_after_sales_decision_tree_catalog_html(catalog), encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a compact after-sales decision tree catalog."
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
    catalog = build_compact_decision_tree_catalog_from_files(
        scenario_workbook_path=args.scenario_workbook,
        knowledge_base_path=args.knowledge_base,
        source_workbook_path=args.source_workbook,
        registry_path=args.registry,
        manifest_path=args.manifest,
        extracted_directory=args.extracted_directory,
    )
    write_compact_decision_tree_catalog_artifacts(
        catalog=catalog,
        output_html=args.output_html,
        output_json=args.output_json,
    )
    print(f"Saved HTML: {args.output_html}")
    print(f"Saved JSON: {args.output_json}")
    print(f"Compact trees: {catalog['summary']['compact_tree_count']}")
    print(f"Scenario evidence records: {catalog['summary']['scenario_evidence_count']}")
    print(f"Compact decision nodes: {catalog['summary']['compact_node_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())