#!/usr/bin/env python3
"""Build all-function after-sales-style Yes/No diagnostic gate trees.

V4 uses supplied after-sales source trees as decision-flow authority where
available. Historical V12 scenarios remain evidence attached to gates rather
than becoming primary tree nodes. Functions with no supplied source flow receive
an explicitly governed evidence-gate template, not invented repair guidance.
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
    / "IDCEVO_Phase09_All_Function_AfterSales_Decision_Gate_Catalog_V4.html"
)
DEFAULT_OUTPUT_JSON = (
    PHASE09_EXPORT_DIR
    / "IDCEVO_Phase09_All_Function_AfterSales_Decision_Gate_Catalog_V4.json"
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


def _primary_branch(path: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    branches = [
        branch
        for branch in path.get("diagnostic_branches", [])
        if isinstance(branch, dict)
    ]
    if not branches:
        fallback = {
            "branch_id": f"EVIDENCE-{_text(path.get('scenario_id'))}",
            "basis": "evidence_collection_template",
            "status": "no_verified_causal_evidence",
            "possible_cause": "No verified technical cause recorded",
            "check_steps": "Collect evidence before further diagnosis.",
            "resolution": "",
            "resolution_status": "no_recorded_recovery",
            "escalation": "Prepare an engineering evidence package",
            "source_reference": {},
            "ticket_evidence": {},
        }
        return fallback, []
    ordered = sorted(branches, key=_branch_sort_key)
    return ordered[0], ordered[1:]


def _source_key(graph: dict[str, Any], node_id: str) -> tuple[str, str]:
    return _text(graph.get("tree_id")), _text(node_id)


def _new_node(
    *,
    node_id: str,
    node_type: str,
    label: str,
    question: str = "",
    check_steps: str = "",
    resolution: str = "",
    escalation: str = "",
    basis: str = "",
    status: str = "",
    yes_next_node: str = "",
    no_next_node: str = "",
    next_node: str = "",
    source_reference: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "node_type": node_type,
        "label": label,
        "question": question,
        "check_steps": check_steps,
        "resolution": resolution,
        "escalation": escalation,
        "basis": basis,
        "status": status,
        "yes_next_node": yes_next_node,
        "no_next_node": no_next_node,
        "next_node": next_node,
        "source_reference": source_reference or {},
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


def _field_precondition_question(problem_type: str) -> str:
    normalized = problem_type.casefold()
    if any(term in normalized for term in ("authentication", "account", "activation")):
        return "Are account, entitlement, activation, market, and vehicle binding prerequisites verified?"
    if any(term in normalized for term in ("connection", "pairing")):
        return "Are compatibility, pairing, and transport-link prerequisites verified?"
    if any(term in normalized for term in ("navigation", "location")):
        return "Are destination or POI input, location context, account state, and connectivity prerequisites verified?"
    if any(term in normalized for term in ("charging", "energy")):
        return "Are vehicle, charging, account, and service eligibility prerequisites verified?"
    if any(term in normalized for term in ("update", "programming")):
        return "Are power, network, storage, package, and vehicle-state update prerequisites verified?"
    if any(term in normalized for term in ("audio", "media")):
        return "Are source selection, audio route, volume, and focus prerequisites verified?"
    if any(term in normalized for term in ("display", "projection")):
        return "Are display mode, application state, and vehicle-display prerequisites verified?"
    return "Are the feature's documented prerequisites and customer operating conditions verified?"


def _field_reproduction_question(problem_type: str) -> str:
    normalized = problem_type.casefold()
    if any(term in normalized for term in ("safety", "emergency")):
        return "Can the reported symptom be safely reproduced or corroborated without creating additional risk?"
    return "Can the reported symptom be reproduced with the recorded trigger condition?"


def _recovery_summary(paths: list[dict[str, Any]]) -> str:
    actions: Counter[str] = Counter()
    for path in paths:
        primary, _ = _primary_branch(path)
        action = _text(primary.get("resolution"))
        status = _text(primary.get("resolution_status"))
        if action and status == "ticket_recorded_workaround_not_approved":
            actions[action] += _integer(path.get("source_ticket_count"))
    if not actions:
        return ""
    visible = actions.most_common(3)
    summary = "; ".join(f"{action} ({count} tickets)" for action, count in visible)
    if len(actions) > len(visible):
        summary += f"; Other recorded candidates ({len(actions) - len(visible)})"
    return summary


def _compact_scenario_evidence(
    path: dict[str, Any], assigned_gate_id: str, assignment_basis: str
) -> dict[str, Any]:
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
        "assigned_gate_id": assigned_gate_id,
        "assignment_basis": assignment_basis,
    }


def _field_gate_ids(service_function_id: str, issue_label: str) -> dict[str, str]:
    issue_suffix = _stable_id("", service_function_id, issue_label).lstrip("-")
    return {
        "issue": f"ISS-{service_function_id}-{issue_suffix}",
        "precondition": f"PRE-{service_function_id}-{issue_suffix}",
        "collect_precondition": f"ACT-PRE-{service_function_id}-{issue_suffix}",
        "reproduction": f"REP-{service_function_id}-{issue_suffix}",
        "collect_reproduction": f"ACT-REP-{service_function_id}-{issue_suffix}",
        "technical": f"TEC-{service_function_id}-{issue_suffix}",
        "collect_technical": f"ACT-TEC-{service_function_id}-{issue_suffix}",
        "recovery": f"REC-{service_function_id}-{issue_suffix}",
        "recovery_candidate": f"OUT-{service_function_id}-{issue_suffix}",
        "escalation": f"ESC-{service_function_id}-{issue_suffix}",
    }


def _append_field_gate_chain(
    *,
    service_function_id: str,
    issue_label: str,
    paths: list[dict[str, Any]],
    no_next_node: str,
    nodes_by_id: dict[str, dict[str, Any]],
    edges: list[dict[str, str]],
) -> str:
    """Append an explicit evidence gate chain and return its issue gate ID."""
    gate_ids = _field_gate_ids(service_function_id, issue_label)
    issue_id = gate_ids["issue"]
    precondition_id = gate_ids["precondition"]
    collect_precondition_id = gate_ids["collect_precondition"]
    reproduction_id = gate_ids["reproduction"]
    collect_reproduction_id = gate_ids["collect_reproduction"]
    technical_id = gate_ids["technical"]
    collect_technical_id = gate_ids["collect_technical"]
    recovery_id = gate_ids["recovery"]
    recovery_candidate_id = gate_ids["recovery_candidate"]
    escalation_id = gate_ids["escalation"]
    recovery_summary = _recovery_summary(paths)
    scenario_ids = [_text(path.get("scenario_id")) for path in paths]
    ticket_count = sum(_integer(path.get("source_ticket_count")) for path in paths)

    nodes_by_id[issue_id] = _new_node(
        node_id=issue_id,
        node_type="Issue Gate",
        label=issue_label,
        question=f"Does the customer complaint match {issue_label}?",
        basis="historical_field_issue_route",
        status="data_derived_issue_gate",
        yes_next_node=precondition_id,
        no_next_node=no_next_node,
    )
    nodes_by_id[precondition_id] = _new_node(
        node_id=precondition_id,
        node_type="Precondition Gate",
        label="Verify prerequisites",
        question=_field_precondition_question(issue_label),
        basis="evidence_gate_template",
        status="requires_current_case_verification",
        yes_next_node=reproduction_id,
        no_next_node=collect_precondition_id,
    )
    nodes_by_id[collect_precondition_id] = _new_node(
        node_id=collect_precondition_id,
        node_type="Evidence Collection",
        label="Collect prerequisite evidence",
        check_steps=(
            "Record account, entitlement, activation, vehicle state, market, and "
            "connectivity prerequisites before continuing."
        ),
        basis="evidence_gate_template",
        status="collect_then_continue",
        next_node=reproduction_id,
    )
    nodes_by_id[reproduction_id] = _new_node(
        node_id=reproduction_id,
        node_type="Reproduction Gate",
        label="Reproduce the complaint",
        question=_field_reproduction_question(issue_label),
        basis="evidence_gate_template",
        status="requires_current_case_verification",
        yes_next_node=technical_id,
        no_next_node=collect_reproduction_id,
    )
    nodes_by_id[collect_reproduction_id] = _new_node(
        node_id=collect_reproduction_id,
        node_type="Evidence Collection",
        label="Capture reproduction context",
        check_steps=(
            "Record exact customer steps, time, vehicle state, device or account context, "
            "and observed versus expected behaviour."
        ),
        basis="evidence_gate_template",
        status="collect_then_continue",
        next_node=technical_id,
    )
    nodes_by_id[technical_id] = _new_node(
        node_id=technical_id,
        node_type="Technical Evidence Gate",
        label="Collect technical evidence",
        question="Are ECU, software version, diagnostic logs, or reproducible technical traces available?",
        basis="evidence_gate_template",
        status="requires_current_case_verification",
        yes_next_node=recovery_id,
        no_next_node=collect_technical_id,
    )
    nodes_by_id[collect_technical_id] = _new_node(
        node_id=collect_technical_id,
        node_type="Evidence Collection",
        label="Collect ECU, software, and log evidence",
        check_steps=(
            "Capture assigned ECU, software version, timestamps, diagnostic logs, and "
            "service responses before assessing a recovery candidate."
        ),
        basis="evidence_gate_template",
        status="collect_then_continue",
        next_node=recovery_id,
    )
    nodes_by_id[recovery_id] = _new_node(
        node_id=recovery_id,
        node_type="Recovery Candidate Gate",
        label="Assess recorded recovery evidence",
        question="Is a ticket-recorded recovery candidate applicable to the verified current case?",
        basis="historical_ticket_evidence",
        status="workaround_review_required",
        yes_next_node=recovery_candidate_id if recovery_summary else escalation_id,
        no_next_node=escalation_id,
    )
    if recovery_summary:
        nodes_by_id[recovery_candidate_id] = _new_node(
            node_id=recovery_candidate_id,
            node_type="Recovery Candidate",
            label=recovery_summary,
            resolution=recovery_summary,
            basis="ticket_recorded_recovery",
            status="not_an_approved_repair",
            escalation="Verify result; if it does not resolve the case, prepare engineering escalation.",
        )
    nodes_by_id[escalation_id] = _new_node(
        node_id=escalation_id,
        node_type="Escalation",
        label="Prepare engineering evidence package",
        escalation=(
            "Provide customer symptom, trigger, precondition state, reproduction result, "
            "ECU/software evidence, logs, and current-case verification results."
        ),
        basis="evidence_gate_template",
        status="engineering_escalation_required",
    )
    _add_edge(edges, issue_id, precondition_id, "YES")
    _add_edge(edges, issue_id, no_next_node, "NO")
    _add_edge(edges, precondition_id, reproduction_id, "YES")
    _add_edge(edges, precondition_id, collect_precondition_id, "NO")
    _add_edge(edges, collect_precondition_id, reproduction_id, "NEXT")
    _add_edge(edges, reproduction_id, technical_id, "YES")
    _add_edge(edges, reproduction_id, collect_reproduction_id, "NO")
    _add_edge(edges, collect_reproduction_id, technical_id, "NEXT")
    _add_edge(edges, technical_id, recovery_id, "YES")
    _add_edge(edges, technical_id, collect_technical_id, "NO")
    _add_edge(edges, collect_technical_id, recovery_id, "NEXT")
    _add_edge(edges, recovery_id, recovery_candidate_id if recovery_summary else escalation_id, "YES")
    _add_edge(edges, recovery_id, escalation_id, "NO")
    for node_id in (
        issue_id,
        precondition_id,
        collect_precondition_id,
        reproduction_id,
        collect_reproduction_id,
        technical_id,
        collect_technical_id,
        recovery_id,
        recovery_candidate_id if recovery_summary else "",
        escalation_id,
    ):
        if not node_id:
            continue
        node = nodes_by_id[node_id]
        node["source_scenario_ids"] = list(scenario_ids)
        node["source_scenario_count"] = len(scenario_ids)
        node["source_ticket_count"] = ticket_count
    return issue_id


def _append_source_graph_gates(
    *,
    service_function_id: str,
    graph: dict[str, Any],
    assignments_by_source: dict[tuple[str, str], list[dict[str, Any]]],
    fallback_node_id: str,
    nodes_by_id: dict[str, dict[str, Any]],
    edges: list[dict[str, str]],
) -> str:
    """Convert one hierarchical source graph into ordered Yes/No question gates."""
    graph_tree_id = _text(graph.get("tree_id"))
    raw_nodes = [node for node in graph.get("nodes", []) if isinstance(node, dict)]
    node_by_source_id = {_text(node.get("node_id")): node for node in raw_nodes}
    children_by_source_id: dict[str, list[str]] = defaultdict(list)
    for node in raw_nodes:
        source_id = _text(node.get("node_id"))
        parent_source_id = _text(node.get("parent_node_id"))
        if source_id and parent_source_id:
            children_by_source_id[parent_source_id].append(source_id)
    for children in children_by_source_id.values():
        children.sort(key=lambda source_id: (
            _text(node_by_source_id[source_id].get("source_path")), source_id
        ))
    root_source_id = "ROOT" if "ROOT" in node_by_source_id else next(
        (
            source_id
            for source_id, node in node_by_source_id.items()
            if not _text(node.get("parent_node_id"))
        ),
        "",
    )
    gate_id_by_source_id = {
        source_id: f"GATE-{service_function_id}-{source_id}"
        for source_id in node_by_source_id
        if source_id != root_source_id
    }

    descendant_cache: dict[str, list[dict[str, Any]]] = {}

    def evidence_for_source(source_id: str) -> list[dict[str, Any]]:
        if source_id in descendant_cache:
            return descendant_cache[source_id]
        direct = list(assignments_by_source.get(_source_key(graph, source_id), []))
        for child_source_id in children_by_source_id.get(source_id, []):
            direct.extend(evidence_for_source(child_source_id))
        unique_by_scenario = {
            _text(item.get("scenario_id")): item
            for item in direct
            if _text(item.get("scenario_id"))
        }
        result = list(unique_by_scenario.values())
        descendant_cache[source_id] = result
        return result

    def build_sibling_chain(source_ids: list[str], default_no: str) -> str:
        next_node_id = default_no
        for source_id in reversed(source_ids):
            source_node = node_by_source_id[source_id]
            gate_id = gate_id_by_source_id[source_id]
            child_source_ids = children_by_source_id.get(source_id, [])
            if child_source_ids:
                yes_next_node = build_sibling_chain(child_source_ids, next_node_id)
                question = f"Does the customer complaint match: {_text(source_node.get('source_text'))}?"
                label = _text(source_node.get("source_text"))
                check_steps = ""
                resolution = ""
                escalation = ""
                node_type = "Question Gate"
            else:
                resolution_id = f"RES-{service_function_id}-{source_id}"
                yes_next_node = resolution_id
                label = _text(source_node.get("source_text"))
                question = f"Does evidence confirm: {label}?"
                check_steps = _text(source_node.get("check_step"))
                resolution = _text(source_node.get("solution"))
                escalation = _text(source_node.get("escalation_path"))
                node_type = "Question Gate"
                nodes_by_id[resolution_id] = _new_node(
                    node_id=resolution_id,
                    node_type="Resolution",
                    label=resolution or escalation or "Follow the approved source-tree route",
                    resolution=resolution,
                    escalation=escalation,
                    basis="after_sales_source_tree",
                    status="source_tree_resolution",
                    source_reference={
                        "reference_tree_id": _text(graph.get("reference_tree_id")),
                        "source_tree_id": graph_tree_id,
                        "source_node_id": source_id,
                        "source_path": _text(source_node.get("source_path")),
                        "source_asset": _text(graph.get("source_asset")),
                    },
                )
            evidence = evidence_for_source(source_id)
            source_reference = {
                "reference_tree_id": _text(graph.get("reference_tree_id")),
                "source_tree_id": graph_tree_id,
                "source_node_id": source_id,
                "source_path": _text(source_node.get("source_path")),
                "source_asset": _text(graph.get("source_asset")),
            }
            nodes_by_id[gate_id] = _new_node(
                node_id=gate_id,
                node_type=node_type,
                label=label,
                question=question,
                check_steps=check_steps,
                resolution=resolution,
                escalation=escalation,
                basis="after_sales_source_tree",
                status="source_tree_question_gate",
                yes_next_node=yes_next_node,
                no_next_node=next_node_id,
                source_reference=source_reference,
            )
            nodes_by_id[gate_id]["source_scenario_ids"] = sorted(
                _text(item.get("scenario_id")) for item in evidence
            )
            nodes_by_id[gate_id]["source_scenario_count"] = len(evidence)
            nodes_by_id[gate_id]["source_ticket_count"] = sum(
                _integer(item.get("source_ticket_count")) for item in evidence
            )
            if not child_source_ids:
                resolution_node = nodes_by_id[yes_next_node]
                resolution_node["source_scenario_ids"] = list(
                    nodes_by_id[gate_id]["source_scenario_ids"]
                )
                resolution_node["source_scenario_count"] = len(evidence)
                resolution_node["source_ticket_count"] = nodes_by_id[gate_id][
                    "source_ticket_count"
                ]
            _add_edge(edges, gate_id, yes_next_node, "YES")
            _add_edge(edges, gate_id, next_node_id, "NO")
            next_node_id = gate_id
        return next_node_id

    first_source_gate = build_sibling_chain(
        children_by_source_id.get(root_source_id, []), fallback_node_id
    )
    reference_gate_id = f"REF-{service_function_id}-{graph_tree_id}"
    source_name = _text(graph.get("tree_name_en")) or graph_tree_id
    nodes_by_id[reference_gate_id] = _new_node(
        node_id=reference_gate_id,
        node_type="Source Flow Gate",
        label=source_name,
        question=f"Does the complaint enter the {source_name} after-sales decision flow?",
        basis="after_sales_source_tree",
        status="source_tree_entry_gate",
        yes_next_node=first_source_gate or fallback_node_id,
        no_next_node=fallback_node_id,
        source_reference={
            "reference_tree_id": _text(graph.get("reference_tree_id")),
            "source_tree_id": graph_tree_id,
            "source_asset": _text(graph.get("source_asset")),
        },
    )
    _add_edge(edges, reference_gate_id, first_source_gate or fallback_node_id, "YES")
    _add_edge(edges, reference_gate_id, fallback_node_id, "NO")
    return reference_gate_id


def build_after_sales_decision_gate_tree(function: dict[str, Any]) -> dict[str, Any]:
    """Create a single after-sales-style decision flow for one service function."""
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
    source_assignments: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    field_paths_by_issue: dict[str, list[dict[str, Any]]] = defaultdict(list)
    scenario_evidence: list[dict[str, Any]] = []
    pending_source_evidence: list[tuple[dict[str, Any], dict[str, Any], str, str]] = []

    for path in scenario_paths:
        primary_branch, _ = _primary_branch(path)
        source_reference = primary_branch.get("source_reference")
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
            _text(primary_branch.get("basis"))
            == "after_sales_experience_source_path"
            and graph
            and source_node_id in graph_node_ids
        ):
            source_assignments[(source_tree_id, source_node_id)].append(path)
            pending_source_evidence.append(
                (path, primary_branch, source_tree_id, source_node_id)
            )
        else:
            issue_label = _text(path.get("problem_type")) or "Other observed field failure"
            field_paths_by_issue[issue_label].append(path)

    nodes_by_id: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, str]] = []
    fallback_id = f"FALLBACK-{service_function_id}"
    nodes_by_id[fallback_id] = _new_node(
        node_id=fallback_id,
        node_type="Evidence Collection",
        label="Capture complaint and route to evidence gates",
        check_steps=(
            "Record the customer symptom, exact trigger, observed versus expected behaviour, "
            "vehicle and account context, and available technical evidence."
        ),
        basis="evidence_gate_template",
        status="unmatched_or_no_source_route",
    )

    issue_items = sorted(
        field_paths_by_issue.items(),
        key=lambda item: (
            -sum(_integer(path.get("source_ticket_count")) for path in item[1]),
            item[0],
        ),
    )
    next_issue_id = f"ESC-GLOBAL-{service_function_id}"
    nodes_by_id[next_issue_id] = _new_node(
        node_id=next_issue_id,
        node_type="Escalation",
        label="Prepare engineering evidence package",
        escalation=(
            "No source-tree match or field issue route was confirmed. Provide complaint, "
            "trigger, reproduction result, ECU/software state, and diagnostic logs."
        ),
        basis="evidence_gate_template",
        status="engineering_escalation_required",
    )
    for issue_label, paths in reversed(issue_items):
        next_issue_id = _append_field_gate_chain(
            service_function_id=service_function_id,
            issue_label=issue_label,
            paths=paths,
            no_next_node=next_issue_id,
            nodes_by_id=nodes_by_id,
            edges=edges,
        )
    field_entry_id = next_issue_id if issue_items else f"ESC-GLOBAL-{service_function_id}"
    nodes_by_id[fallback_id]["next_node"] = field_entry_id
    _add_edge(edges, fallback_id, field_entry_id, "NEXT")

    source_entry_id = field_entry_id
    for graph in reversed(sorted(source_graphs, key=lambda item: _text(item.get("tree_id")))):
        source_entry_id = _append_source_graph_gates(
            service_function_id=service_function_id,
            graph=graph,
            assignments_by_source=source_assignments,
            fallback_node_id=source_entry_id,
            nodes_by_id=nodes_by_id,
            edges=edges,
        )

    root_id = "ROOT"
    nodes_by_id[root_id] = _new_node(
        node_id=root_id,
        node_type="Entry",
        label=service_label,
        question="Start after-sales diagnosis from the customer complaint.",
        basis="service_function_mapping",
        status="after_sales_decision_flow",
        next_node=source_entry_id,
    )
    _add_edge(edges, root_id, source_entry_id, "NEXT")

    for path, primary_branch, source_tree_id, source_node_id in pending_source_evidence:
        assigned_gate_id = f"GATE-{service_function_id}-{source_node_id}"
        scenario_evidence.append(_compact_scenario_evidence(
            path,
            assigned_gate_id,
            "after_sales_source_leaf_match",
        ))
    for issue_label, paths in field_paths_by_issue.items():
        assigned_gate_id = _field_gate_ids(service_function_id, issue_label)["issue"]
        for path in paths:
            scenario_evidence.append(_compact_scenario_evidence(
                path,
                assigned_gate_id,
                "field_issue_evidence_route",
            ))

    for evidence in scenario_evidence:
        node = nodes_by_id.get(evidence["assigned_gate_id"])
        if node is None:
            continue
        scenario_id = evidence["scenario_id"]
        if scenario_id not in node["source_scenario_ids"]:
            node["source_scenario_ids"].append(scenario_id)
            node["source_scenario_count"] += 1
            node["source_ticket_count"] += evidence["source_ticket_count"]

    nodes = sorted(nodes_by_id.values(), key=lambda node: node["node_id"])
    edges_by_id = {edge["edge_id"]: edge for edge in edges}
    return {
        "tree_id": f"DTG-{service_function_id}",
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
        "tree_generation": "after_sales_decision_gate_v4",
        "relationship_policy": (
            "Decision nodes are questions, checks, resolutions, and escalations. "
            "Historical scenarios provide support evidence for a gate; they are not "
            "the causal or chronological tree structure."
        ),
    }


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


def build_decision_gate_catalog_from_scenario_catalog(
    scenario_catalog: dict[str, Any],
) -> dict[str, Any]:
    """Transform V2 evidence paths into one decision-gate flow per function."""
    functions = []
    for function in scenario_catalog.get("functions", []):
        if not isinstance(function, dict):
            continue
        decision_gate_tree = build_after_sales_decision_gate_tree(function)
        functions.append({
            "service_function_id": _text(function.get("service_function_id")),
            "service_function": _text(function.get("service_function")),
            "service_function_label": _text(function.get("service_function_label")),
            "service_domain": _text(function.get("service_domain")),
            "catalog_status": _text(function.get("catalog_status")),
            "source_references": _source_references(function),
            "decision_gate_tree": decision_gate_tree,
        })
    functions.sort(
        key=lambda function: (
            function["service_domain"].casefold(),
            function["service_function_label"].casefold(),
        )
    )
    trees = [function["decision_gate_tree"] for function in functions]
    scenario_evidence = [
        evidence for tree in trees for evidence in tree["scenario_evidence"]
    ]
    return {
        "schema_version": "phase09-after-sales-decision-gate-catalog-v4",
        "data_policy": {
            "source_tree_policy": (
                "Supplied after-sales source trees are converted into Yes/No question "
                "gates using their extracted hierarchy, checks, resolutions, and escalation paths."
            ),
            "historical_evidence_policy": (
                "Historical scenarios support a gate and remain visible in evidence tables; "
                "they do not become the decision-tree structure."
            ),
            "ticket_evidence_policy": (
                "Ticket-recorded recovery is a review-required candidate, not an approved repair."
            ),
            "no_source_policy": (
                "Functions without a supplied source tree use evidence gates and engineering "
                "escalation rather than invented function-specific repairs."
            ),
        },
        "summary": {
            "service_function_count": len(functions),
            "decision_gate_tree_count": len(trees),
            "scenario_evidence_count": len(scenario_evidence),
            "field_ticket_count": sum(
                _integer(evidence["source_ticket_count"])
                for evidence in scenario_evidence
            ),
            "decision_gate_node_count": sum(len(tree["nodes"]) for tree in trees),
            "after_sales_source_flow_count": sum(
                tree["source_graph_count"] for tree in trees
            ),
            "source_matched_evidence_count": sum(
                evidence["assignment_basis"] == "after_sales_source_leaf_match"
                for evidence in scenario_evidence
            ),
            "field_evidence_count": sum(
                evidence["assignment_basis"] == "field_issue_evidence_route"
                for evidence in scenario_evidence
            ),
            "source_reference_count": sum(
                len(function["source_references"]) for function in functions
            ),
        },
        "functions": functions,
    }


def build_decision_gate_catalog_from_files(
    *,
    scenario_workbook_path: Path,
    knowledge_base_path: Path,
    source_workbook_path: Path,
    registry_path: Path,
    manifest_path: Path,
    extracted_directory: Path,
) -> dict[str, Any]:
    """Build V4 from current V12, ticket evidence, and source-tree assets."""
    scenario_catalog = build_scenario_diagnosis_catalog_from_files(
        scenario_workbook_path=scenario_workbook_path,
        knowledge_base_path=knowledge_base_path,
        source_workbook_path=source_workbook_path,
        registry_path=registry_path,
        manifest_path=manifest_path,
        extracted_directory=extracted_directory,
    )
    return build_decision_gate_catalog_from_scenario_catalog(scenario_catalog)


def _serialized_html_data(value: Any) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def render_after_sales_decision_gate_catalog_html(catalog: dict[str, Any]) -> str:
    """Render decision-gate flows and their supporting historical evidence."""
    serialized_catalog = _serialized_html_data(catalog)
    template = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>All-Function After-Sales Decision Gate Catalog</title>
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
.diagram-scroll { min-width:0; overflow:auto; min-height:500px; max-height:820px; }
svg { display:block; min-width:1640px; width:100%; height:auto; }
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
.edge-label { font-size:10px; font-weight:700; fill:#435862; }
@media (max-width:980px) { main { padding:20px 16px 34px; } h1 { font-size:23px; } .summary,.tree-meta { grid-template-columns:repeat(2,minmax(0,1fr)); } .controls,.workspace { grid-template-columns:1fr; } .detail-panel { position:static; } .diagram-scroll { min-height:380px; } }
</style>
</head>
<body>
<main>
  <header>
    <h1>All-Function After-Sales Decision Gate Catalog</h1>
    <p class="subtitle">After-sales decision logic: question -> YES/NO -> next question, source-tree resolution, or evidence escalation. Historical failure scenarios are evidence attached to gates, not the tree structure.</p>
  </header>
  <section class="summary" aria-label="Catalog summary">
    <div><span>Service functions</span><strong id="summary-functions"></strong></div>
    <div><span>Decision-gate trees</span><strong id="summary-trees"></strong></div>
    <div><span>Historical scenarios</span><strong id="summary-scenarios"></strong></div>
    <div><span>Field tickets</span><strong id="summary-tickets"></strong></div>
    <div><span>Source flows</span><strong id="summary-source-flows"></strong></div>
    <div><span>Source-matched evidence</span><strong id="summary-source-evidence"></strong></div>
    <div><span>Field-route evidence</span><strong id="summary-field-evidence"></strong></div>
  </section>
  <section class="controls">
    <label for="function-filter">Find a function<input id="function-filter" type="search" placeholder="Function, domain, or ID"></label>
    <label for="function-selector">Service function<select id="function-selector"></select></label>
  </section>
  <section class="tree-meta">
    <div><span>Function</span><strong id="meta-function"></strong></div>
    <div><span>Decision gates</span><strong id="meta-gates"></strong></div>
    <div><span>Historical scenarios</span><strong id="meta-scenarios"></strong></div>
    <div><span>Historical tickets</span><strong id="meta-tickets"></strong></div>
    <div><span>Source flow coverage</span><strong id="meta-source"></strong></div>
    <div><span>Selected gate</span><strong id="meta-selected"></strong></div>
  </section>
  <p class="governance" id="governance-note"></p>
  <nav class="tabs" role="tablist" aria-label="Tree views">
    <button class="tab" type="button" role="tab" data-view="gate" aria-selected="true">After-Sales Decision Gate Flow</button>
    <button class="tab" type="button" role="tab" data-view="image" aria-selected="false">Original After-Sales Source Image</button>
  </nav>
  <section class="workspace">
    <section class="diagram-panel">
      <header class="panel-head"><span id="diagram-title"></span><select id="source-selector" aria-label="After-sales source reference"></select></header>
      <div class="diagram-scroll" id="diagram-scroll"></div>
    </section>
    <aside class="detail-panel"><header class="panel-head">Selected decision gate</header><dl id="detail-panel"></dl></aside>
  </section>
  <h2 class="section-title">Historical Scenario Evidence</h2>
  <section class="table-shell"><table><thead><tr><th>Assigned gate</th><th>Assignment basis</th><th>Scenario</th><th>Customer symptom</th><th>Trigger</th><th>Tickets</th><th>Confidence</th></tr></thead><tbody id="evidence-rows"></tbody></table></section>
</main>
<script id="catalog-data" type="application/json">__CATALOG_DATA__</script>
<script>
const catalog = JSON.parse(document.getElementById("catalog-data").textContent);
const NS = "http://www.w3.org/2000/svg";
const colors = { Entry:"#d7ead1", "Source Flow Gate":"#b8d7a9", "Question Gate":"#fff0c8", "Issue Gate":"#d8e8f5", "Precondition Gate":"#fff0c8", "Reproduction Gate":"#d5e7f5", "Technical Evidence Gate":"#e4d9f2", "Recovery Candidate Gate":"#d7ead1", "Evidence Collection":"#f7d4d4", "Resolution":"#e2ece8", "Recovery Candidate":"#e2ece8", Escalation:"#f7d4d4" };
let activeFunction = null;
let activeTree = null;
let activeView = "gate";
let activeSourceIndex = 0;
let activeNodeId = "";
function text(value) { return String(value || ""); }
function svgElement(name, attributes = {}) { const element=document.createElementNS(NS,name);Object.entries(attributes).forEach(([key,value])=>element.setAttribute(key,text(value)));return element; }
function wrapLines(value,limit) { const chars=Array.from(text(value));const lines=[];while(chars.length&&lines.length<3)lines.push(chars.splice(0,limit).join(""));return lines.length?lines:[""]; }
function drawText(svg,value,x,y,limit,color="#17242c",fontSize=12) { const lines=wrapLines(value,limit);const label=svgElement("text",{x,y:y-(lines.length-1)*8,"text-anchor":"middle","font-size":fontSize,fill:color});lines.forEach((line,index)=>{const span=svgElement("tspan",{x,dy:index?15:0});span.textContent=line;label.appendChild(span);});svg.appendChild(label); }
function drawEdge(svg,from,to,outcome,markerId) { svg.appendChild(svgElement("line",{x1:from.x+from.right,y1:from.y,x2:to.x-to.left,y2:to.y,stroke:"#536670","stroke-width":1.25,"marker-end":`url(#${markerId})`})); if(outcome!=="NEXT"){const midX=(from.x+from.right+to.x-to.left)/2;const midY=(from.y+to.y)/2-6;drawText(svg,outcome,midX,midY,8,"#435862",10);} }
function nodeShape(svg,node,position) { const actionable=["Source Flow Gate","Question Gate","Issue Gate","Precondition Gate","Reproduction Gate","Technical Evidence Gate","Recovery Candidate Gate"].includes(node.node_type);const group=svgElement("g",{tabindex:0,role:"button",cursor:"pointer","aria-label":`${node.node_type}: ${node.question||node.label}`});const title=svgElement("title");title.textContent=`${node.node_type}: ${node.question||node.label}`;group.appendChild(title);if(actionable){group.appendChild(svgElement("polygon",{points:`${position.x},${position.y-52} ${position.x+position.left},${position.y} ${position.x},${position.y+52} ${position.x-position.left},${position.y}`,fill:colors[node.node_type]||"#eceff1",stroke:node.node_id===activeNodeId?"#b55a19":"#59717c","stroke-width":node.node_id===activeNodeId?3:1.2}));}else{group.appendChild(svgElement("rect",{x:position.x-position.left,y:position.y-44,width:position.left*2,height:88,rx:4,fill:colors[node.node_type]||"#eceff1",stroke:node.node_id===activeNodeId?"#b55a19":"#59717c","stroke-width":node.node_id===activeNodeId?3:1.2}));}svg.appendChild(group);drawText(svg,node.node_type,position.x,position.y-22,21,"#52616b",10);drawText(svg,node.question||node.label,position.x,position.y+5,actionable?21:25);if(node.source_ticket_count)drawText(svg,`${node.source_scenario_count} scenarios / ${node.source_ticket_count} tickets`,position.x,position.y+31,24,"#52616b",10);group.addEventListener("click",()=>{activeNodeId=node.node_id;renderAll();});group.addEventListener("keydown",event=>{if(event.key==="Enter"||event.key===" "){event.preventDefault();activeNodeId=node.node_id;renderAll();}}); }
function nodeById(nodeId) { return activeTree.nodes.find(node=>node.node_id===nodeId)||null; }
function activeSource() { return activeFunction?.source_references?.[activeSourceIndex]||null; }
function renderGateFlow() { const container=document.getElementById("diagram-scroll");container.replaceChildren();document.getElementById("diagram-title").textContent="After-Sales Decision Gate Flow";if(!activeTree)return;const nodes=activeTree.nodes;const byId=new Map(nodes.map(node=>[node.node_id,node]));const outgoing=new Map();nodes.forEach(node=>outgoing.set(node.node_id,[]));activeTree.edges.forEach(edge=>{if(outgoing.has(edge.from_node_id)&&byId.has(edge.to_node_id))outgoing.get(edge.from_node_id).push(edge);});const depth=new Map([["ROOT",0]]);const queue=["ROOT"];for(let index=0;index<queue.length;index+=1){const nodeId=queue[index];(outgoing.get(nodeId)||[]).forEach(edge=>{if(!depth.has(edge.to_node_id)){depth.set(edge.to_node_id,(depth.get(nodeId)||0)+1);queue.push(edge.to_node_id);}});}nodes.forEach(node=>{if(!depth.has(node.node_id))depth.set(node.node_id,0);});const levels=new Map();nodes.forEach(node=>{const level=depth.get(node.node_id)||0;if(!levels.has(level))levels.set(level,[]);levels.get(level).push(node);});const maxDepth=Math.max(0,...levels.keys());const maxRows=Math.max(1,...Array.from(levels.values(),items=>items.length));const width=Math.max(1640,170+(maxDepth+1)*280);const height=Math.max(520,120+maxRows*112);const svg=svgElement("svg",{viewBox:`0 0 ${width} ${height}`,role:"img","aria-label":"After-sales decision gate flow"});const defs=svgElement("defs");const marker=svgElement("marker",{id:"gate-arrow",markerWidth:8,markerHeight:8,refX:7,refY:4,orient:"auto",markerUnits:"strokeWidth"});marker.appendChild(svgElement("path",{d:"M 0 0 L 8 4 L 0 8 z",fill:"#536670"}));defs.appendChild(marker);svg.appendChild(defs);const positions=new Map();Array.from(levels.entries()).forEach(([level,items])=>{items.sort((left,right)=>left.node_id.localeCompare(right.node_id));const gap=height/(items.length+1);items.forEach((node,index)=>positions.set(node.node_id,{x:140+level*280,y:gap*(index+1),left:118,right:118}));});activeTree.edges.forEach(edge=>{const from=positions.get(edge.from_node_id),to=positions.get(edge.to_node_id);if(from&&to)drawEdge(svg,from,to,edge.outcome,"gate-arrow");});nodes.forEach(node=>nodeShape(svg,node,positions.get(node.node_id)));container.appendChild(svg); }
function renderImage() { const container=document.getElementById("diagram-scroll");container.replaceChildren();document.getElementById("diagram-title").textContent="Original After-Sales Source Image";const source=activeSource();if(!source){const empty=document.createElement("p");empty.className="empty-state";empty.textContent="No supplied after-sales source image is mapped to this service function. The decision gate flow uses evidence gates and escalation.";container.appendChild(empty);return;}const image=document.createElement("img");image.className="source-image";image.src=source.source_asset||"";image.alt=`${source.tree_name_en||source.tree_id} original after-sales source image`;container.appendChild(image); }
function detailItem(label,value) { const item=document.createElement("div");const term=document.createElement("dt");const definition=document.createElement("dd");term.textContent=label;definition.textContent=text(value)||"Not recorded";item.append(term,definition);return item; }
function renderDetail() { const root=document.getElementById("detail-panel");root.replaceChildren();const node=nodeById(activeNodeId)||nodeById("ROOT");if(!node){root.append(detailItem("Decision gate","No gate selected."));return;}const source=node.source_reference||{};root.append(detailItem("Gate type",node.node_type),detailItem("Question",node.question),detailItem("Check steps",node.check_steps),detailItem("Resolution",node.resolution),detailItem("Escalation",node.escalation),detailItem("Basis",node.basis),detailItem("Status",node.status),detailItem("Source path",source.source_path),detailItem("Supporting scenarios",node.source_scenario_count),detailItem("Supporting tickets",node.source_ticket_count),detailItem("Scenario IDs",(node.source_scenario_ids||[]).join("; "))); }
function renderEvidenceRows() { const body=document.getElementById("evidence-rows");body.replaceChildren();const filter=document.getElementById("evidence-filter").value.trim().toLocaleLowerCase();(activeTree?.scenario_evidence||[]).filter(item=>!filter||Object.values(item).join(" ").toLocaleLowerCase().includes(filter)).forEach(item=>{const row=document.createElement("tr");if(item.assigned_gate_id===activeNodeId)row.className="selected";[item.assigned_gate_id,item.assignment_basis,item.scenario_id,item.customer_symptom,item.trigger_condition,item.source_ticket_count,item.evidence_confidence].forEach((value,index)=>{const cell=document.createElement("td");cell.textContent=text(value);if(index===5)cell.className="count";row.appendChild(cell);});row.addEventListener("click",()=>{activeNodeId=item.assigned_gate_id;activeView="gate";renderAll();});body.appendChild(row);}); }
function populateSourceSelector() { const selector=document.getElementById("source-selector");selector.replaceChildren();(activeFunction?.source_references||[]).forEach((source,index)=>{const option=document.createElement("option");option.value=String(index);option.textContent=`${source.reference_tree_id||source.tree_id} | ${source.tree_name_en||source.tree_id}`;selector.appendChild(option);});selector.disabled=activeView==="gate"||!(activeFunction?.source_references||[]).length;if(selector.options.length)selector.value=String(activeSourceIndex); }
function renderMeta() { const gates=(activeTree?.nodes||[]).filter(node=>node.node_type.includes("Gate")).length;document.getElementById("meta-function").textContent=activeFunction?.service_function_label||"";document.getElementById("meta-gates").textContent=String(gates);document.getElementById("meta-scenarios").textContent=String(activeTree?.scenario_count||0);document.getElementById("meta-tickets").textContent=String(activeTree?.ticket_count||0);document.getElementById("meta-source").textContent=(activeFunction?.source_references||[]).length?`${activeFunction.source_references.length} source flow(s)`:"Evidence-gate template";document.getElementById("meta-selected").textContent=activeNodeId||"ROOT";document.getElementById("governance-note").textContent=activeTree?.relationship_policy||""; }
function renderAll() { renderMeta();populateSourceSelector();if(activeView==="gate")renderGateFlow();else renderImage();renderDetail();renderEvidenceRows();document.querySelectorAll(".tab").forEach(tab=>tab.setAttribute("aria-selected",String(tab.dataset.view===activeView))); }
function populateFunctionSelector(filterText="") { const selector=document.getElementById("function-selector");const selectedId=activeFunction?.service_function_id||"";const filter=filterText.trim().toLocaleLowerCase();selector.replaceChildren();catalog.functions.filter(item=>!filter||[item.service_function_id,item.service_function_label,item.service_domain].join(" ").toLocaleLowerCase().includes(filter)).forEach(item=>{const option=document.createElement("option");option.value=item.service_function_id;option.textContent=`${item.service_function_label} | ${item.decision_gate_tree.scenario_count} scenarios / ${item.decision_gate_tree.ticket_count} tickets`;selector.appendChild(option);});if(!selector.options.length){const option=document.createElement("option");option.value="";option.textContent="No matching service function";selector.appendChild(option);return;}selector.value=Array.from(selector.options).some(option=>option.value===selectedId)?selectedId:selector.options[0].value; }
function selectFunction(serviceFunctionId) { activeFunction=catalog.functions.find(item=>item.service_function_id===serviceFunctionId)||catalog.functions[0]||null;activeTree=activeFunction?.decision_gate_tree||null;activeSourceIndex=0;activeNodeId="ROOT";renderAll(); }
function initializeSummary() { const summary=catalog.summary||{};document.getElementById("summary-functions").textContent=text(summary.service_function_count);document.getElementById("summary-trees").textContent=text(summary.decision_gate_tree_count);document.getElementById("summary-scenarios").textContent=text(summary.scenario_evidence_count);document.getElementById("summary-tickets").textContent=text(summary.field_ticket_count);document.getElementById("summary-source-flows").textContent=text(summary.after_sales_source_flow_count);document.getElementById("summary-source-evidence").textContent=text(summary.source_matched_evidence_count);document.getElementById("summary-field-evidence").textContent=text(summary.field_evidence_count); }
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
    # The filter sits outside the static template to keep the evidence table usable.
    template = template.replace(
        '<h2 class="section-title">Historical Scenario Evidence</h2>',
        '<h2 class="section-title">Historical Scenario Evidence</h2><label for="evidence-filter">Filter evidence<input id="evidence-filter" type="search" placeholder="Scenario, symptom, trigger, or gate"></label>',
    )
    return template.replace("__CATALOG_DATA__", serialized_catalog)


def write_after_sales_decision_gate_catalog_artifacts(
    *, catalog: dict[str, Any], output_html: Path, output_json: Path
) -> None:
    """Write standalone V4 HTML and JSON artifacts."""
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    output_html.write_text(
        render_after_sales_decision_gate_catalog_html(catalog), encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build an all-function after-sales decision-gate catalog."
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
    catalog = build_decision_gate_catalog_from_files(
        scenario_workbook_path=args.scenario_workbook,
        knowledge_base_path=args.knowledge_base,
        source_workbook_path=args.source_workbook,
        registry_path=args.registry,
        manifest_path=args.manifest,
        extracted_directory=args.extracted_directory,
    )
    write_after_sales_decision_gate_catalog_artifacts(
        catalog=catalog,
        output_html=args.output_html,
        output_json=args.output_json,
    )
    print(f"Saved HTML: {args.output_html}")
    print(f"Saved JSON: {args.output_json}")
    print(f"Decision-gate trees: {catalog['summary']['decision_gate_tree_count']}")
    print(f"Scenario evidence records: {catalog['summary']['scenario_evidence_count']}")
    print(f"Source decision flows: {catalog['summary']['after_sales_source_flow_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())