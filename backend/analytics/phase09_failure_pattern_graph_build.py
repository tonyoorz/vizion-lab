#!/usr/bin/env python3
"""Build evidence-governed Phase 09 failure-pattern graph records."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.analytics.phase09_idcevo_scenario_build import (
    SERVICE_FUNCTION_CATALOG,
    SYMPTOM_PATTERN_LABELS,
    TRIGGER_PATTERN_LABELS,
    _guided_symptom_pattern,
    _guided_trigger_pattern,
    extract_symptom_keyword,
    extract_trigger_keyword,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
PHASE09_EXPORT_DIR = REPO_ROOT / "exports" / "idcevo_phase09"
DEFAULT_SCENARIO_WORKBOOK = (
    PHASE09_EXPORT_DIR
    / "IDCEVO_Phase09_Field_Escape_Scenario_Library_V12_Symptom_to_Solution_Trees.xlsx"
)
DEFAULT_SOURCE_WORKBOOK = (
    PHASE09_EXPORT_DIR
    / "IDCEVO_Phase09_Closure_Risk_20260908_FullLLM_Complete.xlsx"
)

_EMPTY_EVIDENCE_VALUES = {
    "",
    "-",
    "n/a",
    "na",
    "none",
    "none recorded",
    "not recorded",
    "not available",
    "unknown",
    "unspecified",
    "null",
    "无",
    "未知",
    "未记录",
}

_FAILURE_PATTERN_LABELS = {
    "connection-fail": "Connection cannot be established (无法连接)",
    "connection-lost": "Connection lost (连接中断)",
    "connection-timeout": "Connection attempt timed out (连接超时)",
    "cannot-function": "Function cannot be used (无法使用)",
    "feature-unavailable": "Feature unavailable (功能不可用)",
    "audio-none": "No audio / media output (无声音)",
    "audio-abnormal": "Abnormal audio / media output (声音异常)",
    "display-blank": "Blank display / projection (黑屏/无显示)",
    "display-abnormal": "Abnormal display / projection (显示异常)",
    "system-unresponsive": "System unresponsive (无响应)",
    "system-lag": "System lag / delay (卡顿/延迟)",
    "app-crash": "Application crash / exit (崩溃/闪退)",
    "system-reboot": "System reboot / reset (重启/复位)",
    "nav-drift": "Location drift (定位漂移)",
    "nav-fail": "Navigation unavailable (导航不可用)",
    "nav-crash": "Navigation crash (导航崩溃)",
    "login-fail": "Login failure (登录失败)",
    "login-abnormal": "Login abnormal (登录异常)",
    "charging-fail": "Charging unavailable (充电不可用)",
    "update-fail": "Update / programming failure (升级/编程失败)",
    "recognition-fail": "Recognition failure (识别失败)",
    "recognition-error": "Recognition error (识别错误)",
    "startup-fail": "Start-up failure (启动失败)",
    "warning-light": "Warning indication (故障灯/报警)",
    "fail": "Operation failed (失败)",
    "error": "Operation error (错误)",
    "abnormal": "Abnormal operation (异常)",
    "lost": "Data or function lost (丢失)",
    "stopped": "Function stopped (停止)",
    "unavailable": "Function unavailable (不可用)",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _compact_text(value: Any) -> str:
    return re.sub(r"\s+", " ", _text(value))


def _recorded_causal_text(value: Any) -> str:
    """Return source-recorded causal text without inferring a cause."""
    text = _compact_text(value)
    return "" if text.casefold() in _EMPTY_EVIDENCE_VALUES else text


def _recorded_recovery_text(value: Any) -> str:
    text = _compact_text(value)
    return "" if text.casefold() in _EMPTY_EVIDENCE_VALUES else text


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "|".join(_compact_text(part).casefold() for part in parts)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:12].upper()
    return f"{prefix}-{digest}"


def _source_ticket_count(scenario: dict[str, Any]) -> int:
    ticket_ids = scenario.get("ticket_ids") or []
    if ticket_ids:
        return len(ticket_ids)
    return int(scenario.get("ticket_count", 0) or 0)


def _ticket_ids(scenario: dict[str, Any]) -> list[str]:
    return [
        ticket_id
        for ticket_id in (_text(value) for value in scenario.get("ticket_ids") or [])
        if ticket_id
    ]


def _failure_pattern(scenario: dict[str, Any]) -> tuple[str, str, str]:
    symptom_key = extract_symptom_keyword(_text(scenario.get("customer_symptom")))
    trigger_key = extract_trigger_keyword(_text(scenario.get("trigger_condition")))
    symptom_label = SYMPTOM_PATTERN_LABELS.get(symptom_key, symptom_key)
    trigger_label = TRIGGER_PATTERN_LABELS.get(trigger_key, trigger_key)
    problem_type = _text(scenario.get("problem_type")) or "Other observed issue"
    return symptom_key, trigger_key, f"{problem_type}: {symptom_label} | {trigger_label}"


def _failure_mode_status(candidate_count: int) -> str:
    if candidate_count == 0:
        return "no_recorded_causal_evidence"
    if candidate_count == 1:
        return "recorded_candidate_review_required"
    return "conflicting_recorded_candidates_review_required"


def _node(
    node_id: str,
    node_type: str,
    label: str,
    source_scenario_count: int,
    source_ticket_count: int,
    **attributes: Any,
) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "node_type": node_type,
        "label": label,
        "source_scenario_count": source_scenario_count,
        "source_ticket_count": source_ticket_count,
        "attributes": attributes,
    }


def _edge(
    source_node_id: str,
    target_node_id: str,
    relation_type: str,
    evidence_status: str,
    source_ticket_count: int,
    support_ratio: float | None = None,
    source_ticket_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "edge_id": _stable_id(
            "EDGE", source_node_id, target_node_id, relation_type
        ),
        "source_node_id": source_node_id,
        "target_node_id": target_node_id,
        "relation_type": relation_type,
        "evidence_status": evidence_status,
        "source_scenario_count": 1,
        "source_ticket_count": source_ticket_count,
        "support_ratio": support_ratio,
        "source_ticket_ids": source_ticket_ids or [],
    }


def build_failure_pattern_graph(
    scenarios: Iterable[dict[str, Any]],
    ticket_evidence_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build graph records while keeping recorded RCA distinct from verified modes.

    Candidate support is the share of source tickets containing the same recorded
    RCA text. It is descriptive evidence support, never a causal probability.
    """
    scenarios = sorted(
        (dict(scenario) for scenario in scenarios),
        key=lambda item: (
            _text(item.get("service_function_id")),
            _text(item.get("scenario_id")),
        ),
    )
    service_ticket_counts: Counter[str] = Counter()
    for scenario in scenarios:
        service_key = _text(scenario.get("service_function_id")) or _text(
            scenario.get("service_function")
        )
        service_ticket_counts[service_key] += _source_ticket_count(scenario)

    nodes_by_id: dict[str, dict[str, Any]] = {}
    edges_by_id: dict[str, dict[str, Any]] = {}
    failure_patterns: list[dict[str, Any]] = []
    failure_mode_candidates: list[dict[str, Any]] = []
    action_records: list[dict[str, Any]] = []
    service_catalog = [dict(service_function) for service_function in SERVICE_FUNCTION_CATALOG]

    for scenario in scenarios:
        scenario_id = _text(scenario.get("scenario_id"))
        if not scenario_id:
            raise ValueError("Each scenario must have a Scenario ID")
        service_function_id = _text(scenario.get("service_function_id")) or _text(
            scenario.get("service_function")
        )
        service_function = _text(scenario.get("service_function")) or service_function_id
        scenario_ticket_ids = _ticket_ids(scenario)
        if scenario_ticket_ids:
            if len(scenario_ticket_ids) != len(set(scenario_ticket_ids)):
                raise ValueError(
                    f"Duplicate V12 Ticket IDs for Scenario {scenario_id}"
                )
            declared_ticket_count = int(scenario.get("ticket_count", 0) or 0)
            if declared_ticket_count and declared_ticket_count != len(scenario_ticket_ids):
                raise ValueError(
                    f"Ticket mapping count mismatch for Scenario {scenario_id}: "
                    f"declared {declared_ticket_count}, mapped {len(scenario_ticket_ids)}"
                )
            missing_ticket_evidence = [
                ticket_id
                for ticket_id in scenario_ticket_ids
                if ticket_id not in ticket_evidence_by_id
            ]
            if missing_ticket_evidence:
                raise ValueError(
                    f"Missing source ticket evidence for Scenario {scenario_id}: "
                    f"{', '.join(missing_ticket_evidence[:5])}"
                )
        representative_ticket_id = _text(scenario.get("rep_ticket_id"))
        if (
            representative_ticket_id
            and scenario_ticket_ids
            and representative_ticket_id not in scenario_ticket_ids
        ):
            raise ValueError(
                f"Representative Ticket ID is not mapped to Scenario {scenario_id}: "
                f"{representative_ticket_id}"
            )
        source_ticket_count = _source_ticket_count(scenario)
        service_ticket_count = service_ticket_counts[service_function_id]
        symptom_key, trigger_key, failure_pattern = _failure_pattern(scenario)
        failure_pattern_id = _stable_id(
            "FP",
            service_function_id,
            _text(scenario.get("problem_type_id")) or _text(scenario.get("problem_type")),
            symptom_key,
            trigger_key,
        )
        service_node_id = _stable_id("SF", service_function_id)
        scenario_node_id = _stable_id("SC", scenario_id)
        pattern_node_id = _stable_id("NODE", failure_pattern_id)

        nodes_by_id.setdefault(
            service_node_id,
            _node(
                service_node_id,
                "Service Function",
                service_function,
                0,
                service_ticket_count,
                service_function_id=service_function_id,
            ),
        )
        nodes_by_id[service_node_id]["source_scenario_count"] += 1
        nodes_by_id.setdefault(
            scenario_node_id,
            _node(
                scenario_node_id,
                "Scenario",
                scenario_id,
                1,
                source_ticket_count,
                customer_symptom=_text(scenario.get("customer_symptom")),
                trigger_condition=_text(scenario.get("trigger_condition")),
            ),
        )
        nodes_by_id.setdefault(
            pattern_node_id,
            _node(
                pattern_node_id,
                "Failure Pattern",
                failure_pattern,
                0,
                0,
                failure_pattern_id=failure_pattern_id,
                symptom_pattern_key=symptom_key,
                trigger_pattern_key=trigger_key,
            ),
        )
        nodes_by_id[pattern_node_id]["source_scenario_count"] += 1
        nodes_by_id[pattern_node_id]["source_ticket_count"] += source_ticket_count

        service_edge = _edge(
            service_node_id,
            scenario_node_id,
            "contains_scenario",
            "derived_from_v12_scenario_mapping",
            source_ticket_count,
            source_ticket_ids=scenario_ticket_ids,
        )
        pattern_edge = _edge(
            scenario_node_id,
            pattern_node_id,
            "observes_failure_pattern",
            "derived_from_recorded_symptom_and_trigger",
            source_ticket_count,
            source_ticket_ids=scenario_ticket_ids,
        )
        edges_by_id[service_edge["edge_id"]] = service_edge
        edges_by_id[pattern_edge["edge_id"]] = pattern_edge

        candidate_counts: Counter[str] = Counter()
        candidate_ticket_ids: dict[str, list[str]] = {}
        for ticket_id in scenario_ticket_ids:
            candidate = _recorded_causal_text(
                ticket_evidence_by_id.get(ticket_id, {}).get("rca_root_cause")
            )
            if candidate:
                candidate_counts[candidate] += 1
                candidate_ticket_ids.setdefault(candidate, []).append(ticket_id)
        ranked_candidates = sorted(
            candidate_counts.items(), key=lambda item: (-item[1], item[0])
        )
        dominant_candidate, dominant_candidate_count = (
            ranked_candidates[0] if ranked_candidates else ("", 0)
        )
        causal_evidence_ticket_count = sum(candidate_counts.values())
        candidate_support_ratio = (
            dominant_candidate_count / source_ticket_count if source_ticket_count else 0.0
        )
        recovery_action = _recorded_recovery_text(scenario.get("recovery_action"))
        recovery_source_ticket_ids = (
            [representative_ticket_id]
            if recovery_action and representative_ticket_id
            else []
        )
        recovery_provenance_status = (
            "representative_ticket"
            if recovery_source_ticket_ids
            else "representative_ticket_not_recorded"
            if recovery_action
            else "not_applicable"
        )
        pattern_record = {
            "service_function_id": service_function_id,
            "service_function": service_function,
            "scenario_id": scenario_id,
            "problem_type": _text(scenario.get("problem_type")),
            "customer_symptom": _text(scenario.get("customer_symptom")),
            "trigger_condition": _text(scenario.get("trigger_condition")),
            "observed_behaviour": _text(scenario.get("observed_behaviour")),
            "rep_title": _text(scenario.get("rep_title")),
            "failure_pattern_id": failure_pattern_id,
            "failure_pattern": failure_pattern,
            "symptom_pattern_key": symptom_key,
            "trigger_pattern_key": trigger_key,
            "source_ticket_count": source_ticket_count,
            "source_ticket_ids": scenario_ticket_ids,
            "rep_ticket_id": representative_ticket_id,
            "service_function_ticket_count": service_ticket_count,
            "observed_frequency_ratio": (
                source_ticket_count / service_ticket_count if service_ticket_count else 0.0
            ),
            "causal_evidence_ticket_count": causal_evidence_ticket_count,
            "causal_evidence_coverage_ratio": (
                causal_evidence_ticket_count / source_ticket_count
                if source_ticket_count
                else 0.0
            ),
            "failure_mode_candidate": dominant_candidate,
            "failure_mode_status": _failure_mode_status(len(ranked_candidates)),
            "candidate_support_ratio": candidate_support_ratio,
            "cause_probability_status": "not_estimated",
            "recorded_recovery_action": recovery_action,
            "recorded_recovery_source_ticket_ids": recovery_source_ticket_ids,
            "recorded_recovery_evidence_source": (
                "V12 01_Scenario_Library / Recorded Recovery Action"
            ),
            "recorded_recovery_provenance_status": recovery_provenance_status,
            "action_status": (
                "ticket_recorded_recovery_review_required"
                if recovery_action
                else "no_ticket_recorded_recovery"
            ),
            "evidence_confidence": _text(scenario.get("evidence_confidence")),
        }
        failure_patterns.append(pattern_record)

        for candidate, candidate_ticket_count in ranked_candidates:
            candidate_source_ticket_ids = sorted(candidate_ticket_ids[candidate])
            mode_node_id = _stable_id("FM", failure_pattern_id, candidate)
            support_ratio = (
                candidate_ticket_count / source_ticket_count if source_ticket_count else 0.0
            )
            nodes_by_id.setdefault(
                mode_node_id,
                _node(
                    mode_node_id,
                    "Recorded Failure Mode Candidate",
                    candidate,
                    1,
                    candidate_ticket_count,
                    review_status="review_required",
                    cause_probability_status="not_estimated",
                    source_ticket_ids=candidate_source_ticket_ids,
                ),
            )
            candidate_edge = _edge(
                pattern_node_id,
                mode_node_id,
                "has_recorded_failure_mode_candidate",
                "recorded_rca_text_review_required",
                candidate_ticket_count,
                support_ratio,
                candidate_source_ticket_ids,
            )
            edges_by_id[candidate_edge["edge_id"]] = candidate_edge
            failure_mode_candidates.append(
                {
                    "service_function": service_function,
                    "scenario_id": scenario_id,
                    "failure_pattern_id": failure_pattern_id,
                    "failure_pattern": failure_pattern,
                    "failure_mode_candidate": candidate,
                    "candidate_ticket_count": candidate_ticket_count,
                    "source_ticket_count": source_ticket_count,
                    "candidate_support_ratio": support_ratio,
                    "review_status": "review_required",
                    "cause_probability_status": "not_estimated",
                    "evidence_source": "RCA Root Cause raw ticket field",
                    "source_ticket_ids": candidate_source_ticket_ids,
                }
            )

        if not ranked_candidates:
            gap_node_id = _stable_id("GAP", failure_pattern_id, "causal_evidence")
            nodes_by_id.setdefault(
                gap_node_id,
                _node(
                    gap_node_id,
                    "Causal Evidence Gap",
                    "No recorded causal evidence",
                    1,
                    source_ticket_count,
                    cause_probability_status="not_estimated",
                ),
            )
            gap_edge = _edge(
                pattern_node_id,
                gap_node_id,
                "has_causal_evidence_gap",
                "no_recorded_rca_text",
                source_ticket_count,
                source_ticket_ids=scenario_ticket_ids,
            )
            edges_by_id[gap_edge["edge_id"]] = gap_edge

        action_records.append(
            {
                "service_function": service_function,
                "scenario_id": scenario_id,
                "failure_pattern_id": failure_pattern_id,
                "failure_pattern": failure_pattern,
                "recorded_recovery_action": recovery_action,
                "action_status": pattern_record["action_status"],
                "source_ticket_count": source_ticket_count,
                "source_ticket_ids": recovery_source_ticket_ids,
                "evidence_source": "V12 01_Scenario_Library / Recorded Recovery Action",
                "provenance_status": recovery_provenance_status,
            }
        )
        if recovery_action:
            action_node_id = _stable_id("ACT", failure_pattern_id, recovery_action)
            nodes_by_id.setdefault(
                action_node_id,
                _node(
                    action_node_id,
                    "Recorded Recovery Action",
                    recovery_action,
                    1,
                    len(recovery_source_ticket_ids),
                    review_status="not_an_approved_repair",
                    source_ticket_ids=recovery_source_ticket_ids,
                    evidence_source="V12 01_Scenario_Library / Recorded Recovery Action",
                    provenance_status=recovery_provenance_status,
                ),
            )
            action_edge = _edge(
                pattern_node_id,
                action_node_id,
                "has_recorded_recovery_action",
                "ticket_recovery_action_review_required",
                len(recovery_source_ticket_ids),
                source_ticket_ids=recovery_source_ticket_ids,
            )
            edges_by_id[action_edge["edge_id"]] = action_edge

    return {
        "schema_version": "phase09-failure-pattern-graph-v13",
        "model_path": (
            "Service Function -> Issue Group -> Trigger Pattern -> Failure Pattern -> "
            "Ticket Solution"
        ),
        "audit_graph_path": (
            "Service Function -> Scenario -> Failure Pattern -> "
            "{Recorded Failure Mode Candidate / Causal Evidence Gap, "
            "Recorded Recovery Action}"
        ),
        "governance": {
            "failure_pattern_policy": (
                "Derived from recorded problem type, symptom, and trigger only."
            ),
            "failure_mode_policy": (
                "RCA Root Cause text is a review-required recorded candidate, not a "
                "confirmed failure mode."
            ),
            "probability_policy": (
                "Candidate support and observed frequency are evidence metrics, not causal "
                "probability estimates."
            ),
            "action_policy": (
                "Recovery Action is ticket-recorded evidence, not an approved repair."
            ),
        },
        "failure_patterns": failure_patterns,
        "failure_mode_candidates": failure_mode_candidates,
        "action_records": action_records,
        "service_functions": service_catalog,
        "input_provenance": {
            "scenario_records": "V12 Scenario records supplied to the builder",
            "rca_evidence": "Ticket evidence supplied to the builder",
        },
        "nodes": sorted(nodes_by_id.values(), key=lambda item: item["node_id"]),
        "edges": sorted(edges_by_id.values(), key=lambda item: item["edge_id"]),
    }


def _tree_trigger_pattern(record: dict[str, Any]) -> tuple[str, str]:
    trigger_key = _guided_trigger_pattern({
        "trigger_condition": _text(record.get("trigger_condition")),
    })
    trigger_labels = [
        TRIGGER_PATTERN_LABELS.get(
            part,
            part.replace("-", " ").replace("_", " ").title(),
        )
        for part in trigger_key.split("|")
        if part
    ]
    return trigger_key, " + ".join(trigger_labels) or "No trigger recorded"


def _tree_failure_pattern(record: dict[str, Any]) -> tuple[str, str]:
    failure_key = _guided_symptom_pattern({
        "customer_symptom": _text(record.get("customer_symptom")),
        "observed_behaviour": _text(record.get("observed_behaviour")),
        "rep_title": _text(record.get("rep_title")),
    })
    if failure_key not in {"other", "unknown"}:
        return failure_key, _FAILURE_PATTERN_LABELS.get(
            failure_key,
            SYMPTOM_PATTERN_LABELS.get(failure_key, failure_key),
        )
    recorded_symptom = _compact_text(record.get("customer_symptom"))
    if recorded_symptom:
        return (
            f"{failure_key}:{_stable_id('RECORDED', recorded_symptom)}",
            f"Other recorded failure (待分类): {recorded_symptom[:120]}",
        )
    return failure_key, "No failure pattern recorded (未记录)"


def _tree_source_ids(
    records: Iterable[dict[str, Any]], field_name: str
) -> list[str]:
    source_ids = set()
    for record in records:
        for value in record.get(field_name, []) or []:
            item = _text(value)
            if item:
                source_ids.add(item)
    return sorted(source_ids)


def _tree_node(
    *,
    tree_id: str,
    node_id: str,
    node_type: str,
    parent_node_id: str,
    data_key: str,
    question_or_action: str,
    evidence_source: str,
    records: list[dict[str, Any]],
    solution_status: str = "",
    evidence_ticket_ids: list[str] | None = None,
) -> dict[str, Any]:
    source_ticket_ids = _tree_source_ids(records, "source_ticket_ids")
    return {
        "tree_id": tree_id,
        "node_id": node_id,
        "parent_node_id": parent_node_id,
        "node_type": node_type,
        "data_key": data_key,
        "question_or_action": question_or_action,
        "evidence_source": evidence_source,
        "source_scenario_count": len(records),
        "source_ticket_count": sum(
            int(record.get("source_ticket_count", 0) or 0)
            for record in records
        ),
        "source_scenario_ids": sorted(
            {
                _text(record.get("scenario_id"))
                for record in records
                if _text(record.get("scenario_id"))
            }
        ),
        "source_ticket_ids": source_ticket_ids,
        "evidence_ticket_ids": evidence_ticket_ids or source_ticket_ids,
        "solution_status": solution_status,
        "node_source": "v13_failure_pattern_tree",
    }


def _ticket_solution_summary(records: list[dict[str, Any]]) -> str:
    actions = Counter()
    for record in records:
        action = _recorded_recovery_text(record.get("recorded_recovery_action"))
        if action:
            actions[action] += int(record.get("source_ticket_count", 0) or 0)
    ranked_actions = sorted(actions.items(), key=lambda item: (-item[1], item[0]))
    if not ranked_actions:
        return ""
    if len(ranked_actions) == 1:
        action, ticket_count = ranked_actions[0]
        return f"{action} ({ticket_count} tickets)"
    visible_actions = ranked_actions[:3]
    summary = "Recorded ticket solutions: " + "; ".join(
        f"{action} ({ticket_count} tickets)"
        for action, ticket_count in visible_actions
    )
    if len(ranked_actions) > len(visible_actions):
        summary += f"; Other recorded solutions ({len(ranked_actions) - len(visible_actions)} groups)"
    return summary


def build_failure_pattern_trees(
    graph: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build V12-style trees with Trigger Pattern before Failure Pattern."""
    records_by_service: dict[str, list[dict[str, Any]]] = {}
    for record in graph.get("failure_patterns", []):
        service_function_id = _text(record.get("service_function_id"))
        if service_function_id:
            records_by_service.setdefault(service_function_id, []).append(record)

    trees: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    for service in SERVICE_FUNCTION_CATALOG:
        service_function_id = service["service_function_id"]
        tree_id = f"DTFP-{service_function_id}"
        service_records = records_by_service.get(service_function_id, [])
        tree_nodes = [_tree_node(
            tree_id=tree_id,
            node_id="START",
            node_type="Entry",
            parent_node_id="",
            data_key=service_function_id,
            question_or_action=service["service_function"],
            evidence_source="V12 Service Function mapping",
            records=service_records,
        )]
        issue_groups: dict[str, list[dict[str, Any]]] = {}
        for record in service_records:
            issue_groups.setdefault(
                _text(record.get("problem_type")) or "Other / Needs Classification",
                [],
            ).append(record)

        issue_group_items = sorted(
            issue_groups.items(),
            key=lambda item: (
                -sum(
                    int(record.get("source_ticket_count", 0) or 0)
                    for record in item[1]
                ),
                item[0],
            ),
        )
        for issue_index, (issue_label, issue_records) in enumerate(
            issue_group_items, start=1
        ):
            issue_node_id = f"ISS-{issue_index:02d}"
            tree_nodes.append(_tree_node(
                tree_id=tree_id,
                node_id=issue_node_id,
                node_type="Issue Group",
                parent_node_id="START",
                data_key=issue_label,
                question_or_action=issue_label,
                evidence_source="V12 Problem Type",
                records=issue_records,
            ))
            trigger_groups: dict[str, tuple[str, list[dict[str, Any]]]] = {}
            for record in issue_records:
                trigger_key, trigger_label = _tree_trigger_pattern(record)
                if trigger_key not in trigger_groups:
                    trigger_groups[trigger_key] = (trigger_label, [])
                trigger_groups[trigger_key][1].append(record)
            trigger_items = sorted(
                trigger_groups.items(),
                key=lambda item: (
                    -sum(
                        int(record.get("source_ticket_count", 0) or 0)
                        for record in item[1][1]
                    ),
                    item[0],
                ),
            )
            for trigger_index, (trigger_key, (trigger_label, trigger_records)) in enumerate(
                trigger_items, start=1
            ):
                trigger_node_id = f"TRG-{issue_index:02d}-{trigger_index:02d}"
                tree_nodes.append(_tree_node(
                    tree_id=tree_id,
                    node_id=trigger_node_id,
                    node_type="Trigger Pattern",
                    parent_node_id=issue_node_id,
                    data_key=trigger_key,
                    question_or_action=trigger_label,
                    evidence_source="V12 Trigger Condition",
                    records=trigger_records,
                ))
                failure_groups: dict[str, tuple[str, list[dict[str, Any]]]] = {}
                for record in trigger_records:
                    failure_key, failure_label = _tree_failure_pattern(record)
                    if failure_key not in failure_groups:
                        failure_groups[failure_key] = (failure_label, [])
                    failure_groups[failure_key][1].append(record)
                failure_items = sorted(
                    failure_groups.items(),
                    key=lambda item: (
                        -sum(
                            int(record.get("source_ticket_count", 0) or 0)
                            for record in item[1][1]
                        ),
                        item[0],
                    ),
                )
                for failure_index, (
                    failure_key,
                    (failure_label, failure_records),
                ) in enumerate(failure_items, start=1):
                    failure_node_id = (
                        f"FP-{issue_index:02d}-{trigger_index:02d}-{failure_index:02d}"
                    )
                    tree_nodes.append(_tree_node(
                        tree_id=tree_id,
                        node_id=failure_node_id,
                        node_type="Failure Pattern",
                        parent_node_id=trigger_node_id,
                        data_key=failure_key,
                        question_or_action=failure_label,
                        evidence_source="V12 Customer Symptom / Observed Behaviour",
                        records=failure_records,
                    ))
                    records_with_solution = [
                        record
                        for record in failure_records
                        if _recorded_recovery_text(
                            record.get("recorded_recovery_action")
                        )
                    ]
                    if records_with_solution:
                        tree_nodes.append(_tree_node(
                            tree_id=tree_id,
                            node_id=(
                                f"SOL-{issue_index:02d}-{trigger_index:02d}-"
                                f"{failure_index:02d}-01"
                            ),
                            node_type="Ticket Solution",
                            parent_node_id=failure_node_id,
                            data_key="ticket_recorded_solution",
                            question_or_action=_ticket_solution_summary(
                                records_with_solution
                            ),
                            evidence_source=(
                                "V12 01_Scenario_Library / Recorded Recovery Action"
                            ),
                            records=records_with_solution,
                            solution_status="ticket_recorded_solution",
                            evidence_ticket_ids=_tree_source_ids(
                                records_with_solution,
                                "recorded_recovery_source_ticket_ids",
                            ),
                        ))
                    records_without_solution = [
                        record
                        for record in failure_records
                        if not _recorded_recovery_text(
                            record.get("recorded_recovery_action")
                        )
                    ]
                    if records_without_solution:
                        tree_nodes.append(_tree_node(
                            tree_id=tree_id,
                            node_id=(
                                f"SOL-{issue_index:02d}-{trigger_index:02d}-"
                                f"{failure_index:02d}-02"
                            ),
                            node_type="Ticket Solution",
                            parent_node_id=failure_node_id,
                            data_key="no_ticket_solution_recorded",
                            question_or_action="",
                            evidence_source="",
                            records=records_without_solution,
                            solution_status="no_ticket_solution_recorded",
                            evidence_ticket_ids=[],
                        ))

        trees.append({
            "tree_id": tree_id,
            "service_function_id": service_function_id,
            "service_function": service["service_function"],
            "service_function_label": service["display_name"],
            "service_domain": service["service_domain"],
            "catalog_status": service["catalog_status"],
            "scenario_count": len(service_records),
            "ticket_count": sum(
                int(record.get("source_ticket_count", 0) or 0)
                for record in service_records
            ),
            "issue_group_count": len(issue_group_items),
            "trigger_pattern_count": sum(
                1 for node in tree_nodes if node["node_type"] == "Trigger Pattern"
            ),
            "failure_pattern_count": sum(
                1 for node in tree_nodes if node["node_type"] == "Failure Pattern"
            ),
            "node_count": len(tree_nodes),
            "edge_count": max(0, len(tree_nodes) - 1),
            "tree_generation": "v13_failure_pattern_tree_v1",
            "diagnostic_layers": [
                "Issue Group",
                "Trigger Pattern",
                "Failure Pattern",
                "Ticket Solution",
            ],
            "solution_source_policy": (
                "Ticket Solution text is copied from V12 Recorded Recovery Action. "
                "It can be a workaround and is not an approved repair instruction."
            ),
            "source_status": (
                "Data-derived from V12 customer symptoms and trigger conditions. "
                "Failure Pattern is customer-visible behaviour, not a technical root cause."
            ),
        })
        nodes.extend(tree_nodes)
    return trees, nodes


def build_service_function_decision_trees(
    graph: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build one readable V13 evidence tree for each Service Function."""
    candidates_by_scenario: dict[str, list[dict[str, Any]]] = {}
    for candidate in graph.get("failure_mode_candidates", []):
        scenario_id = _text(candidate.get("scenario_id"))
        if scenario_id:
            candidates_by_scenario.setdefault(scenario_id, []).append(candidate)

    paths_by_service: dict[str, list[dict[str, Any]]] = {}
    service_metadata: dict[str, dict[str, str]] = {}
    service_function_order = []
    for service in graph.get("service_functions", []):
        service_function_id = _text(service.get("service_function_id"))
        if service_function_id:
            service_function_order.append(service_function_id)
            service_metadata[service_function_id] = {
                "service_function": _text(service.get("service_function")),
                "service_function_label": _text(service.get("display_name")),
                "catalog_status": _text(service.get("catalog_status")),
            }

    for record in graph.get("failure_patterns", []):
        service_function_id = _text(record.get("service_function_id"))
        if not service_function_id:
            continue
        service_metadata.setdefault(
            service_function_id,
            {
                "service_function": _text(record.get("service_function")),
                "service_function_label": _text(record.get("service_function")),
                "catalog_status": "",
            },
        )
        candidate_details = sorted(
            candidates_by_scenario.get(_text(record.get("scenario_id")), []),
            key=lambda item: (
                -int(item.get("candidate_ticket_count", 0) or 0),
                _text(item.get("failure_mode_candidate")),
            ),
        )
        paths_by_service.setdefault(service_function_id, []).append({
            "scenario_id": _text(record.get("scenario_id")),
            "customer_symptom": _text(record.get("customer_symptom")),
            "trigger_condition": _text(record.get("trigger_condition")),
            "failure_pattern_id": _text(record.get("failure_pattern_id")),
            "failure_pattern": _text(record.get("failure_pattern")),
            "source_ticket_count": int(record.get("source_ticket_count", 0) or 0),
            "causal_evidence_ticket_count": int(
                record.get("causal_evidence_ticket_count", 0) or 0
            ),
            "causal_evidence_coverage_ratio": float(
                record.get("causal_evidence_coverage_ratio", 0) or 0
            ),
            "failure_mode_candidates": [
                _text(item.get("failure_mode_candidate"))
                for item in candidate_details
            ],
            "failure_mode_candidate_details": candidate_details,
            "causal_evidence_gap": not candidate_details,
            "recorded_recovery_action": _text(
                record.get("recorded_recovery_action")
            ),
            "recorded_recovery_source_ticket_ids": list(
                record.get("recorded_recovery_source_ticket_ids") or []
            ),
            "recorded_recovery_evidence_source": _text(
                record.get("recorded_recovery_evidence_source")
            ),
            "recorded_recovery_provenance_status": _text(
                record.get("recorded_recovery_provenance_status")
            ),
            "action_status": _text(record.get("action_status")),
            "evidence_confidence": _text(record.get("evidence_confidence")),
        })

    trees = []
    ordered_service_function_ids = [
        *dict.fromkeys(service_function_order),
        *sorted(set(service_metadata).difference(service_function_order)),
    ]
    for service_function_id in ordered_service_function_ids:
        metadata = service_metadata[service_function_id]
        paths = sorted(
            paths_by_service.get(service_function_id, []),
            key=lambda item: (-item["source_ticket_count"], item["scenario_id"]),
        )
        trees.append({
            "tree_id": f"DTFP-{service_function_id}",
            "service_function_id": service_function_id,
            "service_function": metadata["service_function"] or service_function_id,
            "service_function_label": (
                metadata["service_function_label"]
                or metadata["service_function"]
                or service_function_id
            ),
            "catalog_status": metadata["catalog_status"],
            "scenario_count": len(paths),
            "ticket_count": sum(path["source_ticket_count"] for path in paths),
            "paths": paths,
            "tree_generation": "v13_failure_pattern_evidence_tree",
            "source_status": (
                "Observed V12 scenarios are shown as evidence paths. RCA text remains "
                "review-required candidate evidence, and recovery text is not an approved repair."
            ),
        })
    return trees


def _serialized_html_data(value: Any) -> str:
    """Serialize untrusted ticket text safely for an HTML script-data element."""
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def render_service_function_decision_tree_catalog_html(graph: dict[str, Any]) -> str:
    """Render V13 as selectable, per-Service-Function evidence trees."""
    serialized_trees = _serialized_html_data(
        build_service_function_decision_trees(graph)
    )
    template = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>IDCEVO Phase 09 V13 Failure Pattern Decision Trees</title>
<style>
:root { color: #18212b; background: #f2f4f5; font-family: Aptos, "Noto Sans SC", "Microsoft YaHei", sans-serif; }
* { box-sizing: border-box; }
body { margin: 0; }
main { max-width: 1540px; margin: 0 auto; padding: 32px 28px 52px; }
header { border-bottom: 4px solid #167a72; padding-bottom: 20px; }
h1 { margin: 0; color: #123c4a; font-size: 28px; font-weight: 700; letter-spacing: 0; }
p { line-height: 1.5; }
.subtitle { margin: 8px 0 0; color: #52616b; font-size: 15px; }
.controls { display: grid; grid-template-columns: minmax(260px, 0.8fr) minmax(360px, 1.2fr); gap: 18px; margin: 24px 0 16px; }
label { color: #33414b; font-size: 13px; font-weight: 700; }
select { display: block; width: 100%; min-height: 42px; margin-top: 7px; padding: 8px 12px; border: 1px solid #7d919d; border-radius: 5px; background: #fff; color: #18212b; font: inherit; }
.tree-meta { display: grid; grid-template-columns: repeat(5, minmax(130px, 1fr)); gap: 1px; border: 1px solid #c7d0d6; background: #c7d0d6; }
.tree-meta div { min-width:0; min-height: 72px; padding: 12px 14px; background: #fff; }
.tree-meta span { display: block; color: #5a6871; font-size: 12px; }
.tree-meta strong { display: block; margin-top: 6px; color: #183c4a; font-size: 15px; line-height: 1.25; overflow-wrap:anywhere; }
.status { margin: 16px 0 0; padding: 10px 12px; border-left: 4px solid #d68b25; background: #fff4de; color: #5c3e13; font-size: 13px; }
.diagram-shell { margin-top: 20px; overflow-x: auto; border: 1px solid #b9c6cc; background: #fff; }
svg { display: block; min-width: 1260px; width: 100%; height: auto; }
.detail-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1px; margin-top: 20px; border: 1px solid #c7d0d6; background: #c7d0d6; }
.detail-grid div { min-width:0; padding: 12px 14px; background: #fff; font-size: 14px; line-height: 1.45; overflow-wrap:anywhere; }
.detail-grid strong { display: block; margin-bottom: 5px; color: #52616b; font-size: 12px; }
.node-table { width: 100%; margin-top: 20px; border-collapse: collapse; background: #fff; font-size: 13px; }
.node-table th, .node-table td { min-width:0; padding: 9px 10px; border: 1px solid #c7d0d6; text-align: left; vertical-align: top; line-height: 1.4; overflow-wrap:anywhere; }
.node-table th { background: #123c4a; color: #fff; }
.node-table td:first-child, .node-table td:nth-child(3), .node-table td:nth-child(4) { white-space: nowrap; }
@media (max-width: 760px) { main { padding: 22px 16px 36px; } h1 { font-size: 23px; } .controls, .tree-meta, .detail-grid { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<main>
  <header>
    <h1>IDCEVO Phase 09 V13 Failure Pattern Decision Trees</h1>
    <p class="subtitle">Service Function -&gt; Scenario -&gt; Failure Pattern -&gt; RCA candidate or evidence gap, with ticket-recorded recovery shown as a separate evidence branch.</p>
  </header>
  <section class="controls">
    <label for="tree-selector">Service Function<select id="tree-selector"></select></label>
    <label for="scenario-selector">Scenario branch<select id="scenario-selector"></select></label>
  </section>
  <section class="tree-meta">
    <div><span>Service Function</span><strong id="tree-title"></strong></div>
    <div><span>Mapped Scenarios</span><strong id="scenario-count"></strong></div>
    <div><span>Mapped Tickets</span><strong id="ticket-count"></strong></div>
    <div><span>Visible Scenario</span><strong id="visible-scenario"></strong></div>
    <div><span>Tree Generation</span><strong id="tree-generation"></strong></div>
  </section>
  <p class="status" id="source-status"></p>
  <section class="diagram-shell"><svg id="decision-tree" role="img" aria-label="V13 Service Function Failure Pattern Decision Tree"></svg></section>
  <section id="detail-grid" class="detail-grid" aria-label="Selected Scenario Evidence"></section>
  <table class="node-table"><thead><tr><th>Tree Stage</th><th>Evidence / Record</th><th>Source Tickets</th><th>Support</th><th>Governance Status</th></tr></thead><tbody id="node-rows"></tbody></table>
</main>
<script id="tree-data" type="application/json">__TREE_DATA__</script>
<script>
const trees = JSON.parse(document.getElementById("tree-data").textContent);
const NS = "http://www.w3.org/2000/svg";
const treeSelector = document.getElementById("tree-selector");
const scenarioSelector = document.getElementById("scenario-selector");
let activeTree = null;
function svgElement(name, attributes = {}) { const element = document.createElementNS(NS, name); Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, String(value))); return element; }
function text(value) { return String(value || ""); }
function ratio(value) { return `${(Number(value || 0) * 100).toFixed(1)}%`; }
function shortText(value, limit = 88) { const characters = Array.from(text(value)); return characters.length > limit ? `${characters.slice(0, limit).join("")}...` : characters.join(""); }
function wrapLines(value, limit = 25) { const characters = Array.from(text(value)); const lines = []; while (characters.length && lines.length < 4) lines.push(characters.splice(0, limit).join("")); return lines.length ? lines : [""]; }
function addText(svg, value, x, y, limit = 25, color = "#18212b") { const lines = wrapLines(value, limit); const node = svgElement("text", { x, y: y - Math.max(0, lines.length - 1) * 8, "text-anchor": "middle", "font-size": 12, fill: color }); lines.forEach((line, index) => { const span = svgElement("tspan", { x, dy: index ? 15 : 0 }); span.textContent = line; node.appendChild(span); }); svg.appendChild(node); }
function drawEdge(svg, source, target, markerId) { svg.appendChild(svgElement("line", { x1: source.x + source.right, y1: source.y, x2: target.x - target.left, y2: target.y, stroke: "#536670", "stroke-width": 1.35, "marker-end": `url(#${markerId})` })); }
function drawNode(svg, node) { const group = svgElement("g"); const title = svgElement("title"); title.textContent = `${node.type}: ${node.label}`; group.appendChild(title); if (node.shape === "ellipse") group.appendChild(svgElement("ellipse", { cx: node.x, cy: node.y, rx: node.left, ry: 47, fill: node.fill, stroke: "#55716d", "stroke-width": 1.4 })); else if (node.shape === "diamond") group.appendChild(svgElement("polygon", { points: `${node.x},${node.y - 56} ${node.x + node.left},${node.y} ${node.x},${node.y + 56} ${node.x - node.left},${node.y}`, fill: node.fill, stroke: "#55716d", "stroke-width": 1.4 })); else group.appendChild(svgElement("rect", { x: node.x - node.left, y: node.y - 48, width: node.left * 2, height: 96, rx: 6, fill: node.fill, stroke: "#55716d", "stroke-width": 1.4 })); svg.appendChild(group); addText(svg, node.type, node.x, node.y - 29, 26, "#52616b"); addText(svg, node.label, node.x, node.y + 5, node.textLimit || 25); if (node.detail) addText(svg, node.detail, node.x, node.y + 35, 28, "#52616b"); }
function emptyTree(svg, tree) { svg.setAttribute("viewBox", "0 0 1260 360"); addText(svg, tree.service_function_label, 250, 160, 34); addText(svg, "No V12 field scenarios mapped to this Service Function", 760, 160, 58, "#52616b"); }
function layoutFailurePatternTree(tree, path) { const svg = document.getElementById("decision-tree"); svg.replaceChildren(); if (!path) { emptyTree(svg, tree); return; } const candidates = path.failure_mode_candidate_details.slice(0, 4); const candidateRows = Math.max(1, candidates.length || 1); const height = Math.max(520, 210 + candidateRows * 120); svg.setAttribute("viewBox", `0 0 1380 ${height}`); const markerId = "failure-pattern-arrow"; const defs = svgElement("defs"); const marker = svgElement("marker", { id: markerId, markerWidth: 8, markerHeight: 8, refX: 7, refY: 4, orient: "auto", markerUnits: "strokeWidth" }); marker.appendChild(svgElement("path", { d: "M 0 0 L 8 4 L 0 8 z", fill: "#536670" })); defs.appendChild(marker); svg.appendChild(defs); const middleY = height / 2; const root = { type: "Service Function", label: tree.service_function_label, detail: `${tree.ticket_count} tickets`, x: 130, y: middleY, left: 105, right: 105, shape: "ellipse", fill: "#d7ead1" }; const scenario = { type: "Scenario", label: path.scenario_id, detail: `${path.source_ticket_count} tickets`, x: 410, y: middleY, left: 122, right: 122, shape: "diamond", fill: "#b8d7a9" }; const pattern = { type: "Failure Pattern", label: path.failure_pattern, detail: "Observed symptom + trigger", x: 720, y: middleY, left: 132, right: 132, shape: "rect", fill: "#d5e7f5", textLimit: 28 }; drawEdge(svg, root, scenario, markerId); drawEdge(svg, scenario, pattern, markerId); drawNode(svg, root); drawNode(svg, scenario); drawNode(svg, pattern); const candidateYStart = middleY - ((candidateRows - 1) * 110) / 2; if (candidates.length) { candidates.forEach((candidate, index) => { const candidateNode = { type: "Recorded Failure Mode Candidate", label: candidate.failure_mode_candidate, detail: `${candidate.candidate_ticket_count}/${candidate.source_ticket_count} tickets; ${ratio(candidate.candidate_support_ratio)}`, x: 1060, y: candidateYStart + index * 110, left: 138, right: 138, shape: "rect", fill: "#f5e5bf", textLimit: 28 }; drawEdge(svg, pattern, candidateNode, markerId); drawNode(svg, candidateNode); }); if (path.failure_mode_candidate_details.length > candidates.length) addText(svg, `+${path.failure_mode_candidate_details.length - candidates.length} more recorded candidates`, 1060, candidateYStart + candidates.length * 110, 36, "#52616b"); } else { const gap = { type: "Causal Evidence Gap", label: "No recorded causal evidence", detail: "Cause probability: not estimated", x: 1060, y: middleY - 100, left: 138, right: 138, shape: "rect", fill: "#f7d4d4", textLimit: 28 }; drawEdge(svg, pattern, gap, markerId); drawNode(svg, gap); } const action = { type: "Recorded Recovery Action", label: path.recorded_recovery_action || "No ticket-recorded recovery", detail: path.recorded_recovery_action ? "Not an approved repair" : "Evidence gap", x: 1060, y: middleY + Math.max(110, candidateRows * 55), left: 138, right: 138, shape: "rect", fill: "#e4e9ec", textLimit: 28 }; drawEdge(svg, pattern, action, markerId); drawNode(svg, action); }
function detailItem(label, value) { const item = document.createElement("div"); const title = document.createElement("strong"); title.textContent = label; const content = document.createElement("span"); content.textContent = value || "Not recorded"; item.append(title, content); return item; }
function renderDetails(path) { const root = document.getElementById("detail-grid"); root.replaceChildren(); if (!path) { root.appendChild(detailItem("Scenario Evidence", "No V12 field scenario is mapped to this Service Function.")); return; } root.append(detailItem("Customer Symptom", path.customer_symptom), detailItem("Trigger Condition", path.trigger_condition), detailItem("Failure Pattern", path.failure_pattern), detailItem("RCA Evidence", `${path.causal_evidence_ticket_count}/${path.source_ticket_count} source tickets (${ratio(path.causal_evidence_coverage_ratio)}); causal probability not estimated`), detailItem("Recorded Recovery", path.recorded_recovery_action || "No ticket-recorded recovery"), detailItem("Extraction Confidence", path.evidence_confidence)); }
function appendCell(row, value) { const cell = document.createElement("td"); cell.textContent = value || ""; row.appendChild(cell); }
function renderRows(path) { const body = document.getElementById("node-rows"); body.replaceChildren(); if (!path) return; const stages = [["Service Function", activeTree.service_function_label, String(activeTree.ticket_count), "", "V12 service-function mapping"], ["Scenario", path.scenario_id, String(path.source_ticket_count), "", "V12 Scenario"], ["Failure Pattern", path.failure_pattern, String(path.source_ticket_count), "", "Derived from recorded symptom + trigger"], ["Recorded Recovery Action", path.recorded_recovery_action || "No ticket-recorded recovery", String(path.source_ticket_count), "", path.action_status]]; if (path.failure_mode_candidate_details.length) path.failure_mode_candidate_details.forEach((candidate) => stages.push(["Recorded Failure Mode Candidate", candidate.failure_mode_candidate, `${candidate.candidate_ticket_count}/${candidate.source_ticket_count}`, ratio(candidate.candidate_support_ratio), candidate.review_status])); else stages.push(["Causal Evidence Gap", "No recorded causal evidence", String(path.source_ticket_count), "", "cause probability not estimated"]); stages.forEach((stage) => { const row = document.createElement("tr"); stage.forEach((value) => appendCell(row, value)); body.appendChild(row); }); }
function populateScenarioSelector(tree) { scenarioSelector.replaceChildren(); if (!tree.paths.length) { const option = document.createElement("option"); option.value = ""; option.textContent = "No mapped V12 field scenario"; scenarioSelector.appendChild(option); scenarioSelector.disabled = true; return; } scenarioSelector.disabled = false; tree.paths.forEach((path) => { const option = document.createElement("option"); option.value = path.scenario_id; option.textContent = `${path.scenario_id} | ${shortText(path.customer_symptom)} (${path.source_ticket_count} tickets)`; scenarioSelector.appendChild(option); }); }
function activePath() { return activeTree.paths.find((path) => path.scenario_id === scenarioSelector.value) || activeTree.paths[0] || null; }
function renderActivePath() { const path = activePath(); document.getElementById("visible-scenario").textContent = path ? `${path.scenario_id} (${path.source_ticket_count} tickets)` : "No mapped scenario"; layoutFailurePatternTree(activeTree, path); renderDetails(path); renderRows(path); }
function selectTree(treeId) { activeTree = trees.find((tree) => tree.tree_id === treeId) || trees[0] || null; if (!activeTree) return; document.getElementById("tree-title").textContent = activeTree.service_function_label; document.getElementById("scenario-count").textContent = String(activeTree.scenario_count); document.getElementById("ticket-count").textContent = String(activeTree.ticket_count); document.getElementById("tree-generation").textContent = activeTree.tree_generation; document.getElementById("source-status").textContent = activeTree.source_status; populateScenarioSelector(activeTree); renderActivePath(); }
trees.forEach((tree) => { const option = document.createElement("option"); option.value = tree.tree_id; option.textContent = `${tree.service_function_label} (${tree.scenario_count} scenarios / ${tree.ticket_count} tickets)`; treeSelector.appendChild(option); }); const defaultTree = trees.find((tree) => tree.service_function_id === "CARPLAY" && tree.paths.length) || trees.find((tree) => tree.paths.length) || trees[0]; if (defaultTree) { treeSelector.value = defaultTree.tree_id; selectTree(defaultTree.tree_id); } treeSelector.addEventListener("change", (event) => selectTree(event.target.value)); scenarioSelector.addEventListener("change", renderActivePath);
</script>
</body>
</html>"""
    return template.replace("__TREE_DATA__", serialized_trees)


def render_failure_pattern_tree_catalog_html(graph: dict[str, Any]) -> str:
        """Render a V12-style tree without exposing Scenario as a tree layer."""
        trees, nodes = build_failure_pattern_trees(graph)
        nodes_by_tree: dict[str, list[dict[str, Any]]] = {}
        for node in nodes:
                nodes_by_tree.setdefault(node["tree_id"], []).append(node)
        tree_data = [{**tree, "nodes": nodes_by_tree[tree["tree_id"]]} for tree in trees]
        serialized_trees = _serialized_html_data(tree_data)
        template = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>IDCEVO Phase 09 V13 Failure Pattern Trees</title>
<style>
:root { color:#18212b; background:#f2f4f5; font-family:Aptos,"Noto Sans SC","Microsoft YaHei",sans-serif; }
* { box-sizing:border-box; }
html,body { overflow-x:hidden; }
body { margin:0; }
main { max-width:1540px; margin:0 auto; padding:32px 28px 52px; }
header { border-bottom:4px solid #167a72; padding-bottom:20px; }
h1 { margin:0; color:#123c4a; font-size:28px; font-weight:700; letter-spacing:0; }
p { line-height:1.5; }
.subtitle { margin:8px 0 0; color:#52616b; font-size:15px; }
.controls { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:18px; margin:24px 0 16px; }
label { min-width:0; color:#33414b; font-size:13px; font-weight:700; }
select { display:block; width:100%; min-height:42px; margin-top:7px; padding:8px 12px; border:1px solid #7d919d; border-radius:5px; background:#fff; color:#18212b; font:inherit; }
.tree-meta { display:grid; grid-template-columns:repeat(5,minmax(130px,1fr)); gap:1px; border:1px solid #c7d0d6; background:#c7d0d6; }
.tree-meta div { min-width:0; min-height:72px; padding:12px 14px; background:#fff; }
.tree-meta span { display:block; color:#5a6871; font-size:12px; }
.tree-meta strong { display:block; margin-top:6px; color:#183c4a; font-size:15px; line-height:1.25; overflow-wrap:anywhere; }
.status { margin:16px 0 0; padding:10px 12px; border-left:4px solid #d68b25; background:#fff4de; color:#5c3e13; font-size:13px; }
.diagram-shell { min-width:0; max-width:100%; margin-top:20px; overflow-x:auto; border:1px solid #b9c6cc; background:#fff; }
svg { display:block; min-width:1320px; width:100%; height:auto; }
.node-table { width:100%; margin-top:20px; border-collapse:collapse; background:#fff; font-size:13px; }
.node-table th,.node-table td { min-width:0; padding:9px 10px; border:1px solid #c7d0d6; text-align:left; vertical-align:top; line-height:1.4; overflow-wrap:anywhere; }
.node-table th { background:#123c4a; color:#fff; }
.node-table td:first-child,.node-table td:nth-child(3),.node-table td:nth-child(4) { white-space:nowrap; }
@media (max-width:760px) { main { padding:22px 16px 36px; } h1 { font-size:23px; } .controls,.tree-meta { grid-template-columns:1fr; } }
</style>
</head>
<body>
<main>
    <header>
        <h1>IDCEVO Phase 09 V13 Failure Pattern Trees</h1>
        <p class="subtitle">Service Function -&gt; Issue Group -&gt; Trigger Pattern -&gt; Failure Pattern -&gt; Ticket Solution</p>
    </header>
    <section class="controls">
        <label for="tree-selector">Service Function<select id="tree-selector"></select></label>
        <label for="issue-selector">Issue Group<select id="issue-selector"></select></label>
        <label for="trigger-selector">Trigger Pattern<select id="trigger-selector"></select></label>
    </section>
    <section class="tree-meta">
        <div><span>Service Function</span><strong id="tree-title"></strong></div>
        <div><span>Mapped Scenarios</span><strong id="scenario-count"></strong></div>
        <div><span>Mapped Tickets</span><strong id="ticket-count"></strong></div>
        <div><span>Failure Patterns</span><strong id="failure-count"></strong></div>
        <div><span>Ticket Solutions</span><strong id="solution-count"></strong></div>
    </section>
    <p class="status" id="source-status"></p>
    <section class="diagram-shell"><svg id="decision-tree" role="img" aria-label="Failure Pattern Decision Tree"></svg></section>
    <table class="node-table"><thead><tr><th>Node</th><th>Parent</th><th>Type</th><th>Classification / Ticket Solution</th><th>Evidence Source</th><th>Scenario Count</th><th>Ticket Count</th><th>Source Scenario IDs</th><th>Evidence Ticket IDs</th><th>Solution Status</th></tr></thead><tbody id="node-rows"></tbody></table>
</main>
<script id="tree-data" type="application/json">__TREE_DATA__</script>
<script>
const trees = JSON.parse(document.getElementById("tree-data").textContent);
const NS = "http://www.w3.org/2000/svg";
const treeSelector = document.getElementById("tree-selector");
const issueSelector = document.getElementById("issue-selector");
const triggerSelector = document.getElementById("trigger-selector");
let activeTree = null;
function svgElement(name, attributes = {}) { const element = document.createElementNS(NS, name); Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, String(value))); return element; }
function childrenOf(tree, parentId, nodeType) { return tree.nodes.filter(node => node.parent_node_id === parentId && (!nodeType || node.node_type === nodeType)).sort((left, right) => right.source_ticket_count - left.source_ticket_count || left.node_id.localeCompare(right.node_id)); }
function rootNode(tree) { return tree.nodes.find(node => node.node_type === "Entry") || null; }
function nodeById(tree, nodeId) { return tree.nodes.find(node => node.node_id === nodeId) || null; }
function displayText(node) { return node.question_or_action || (node.node_type === "Ticket Solution" ? "No ticket-recorded solution" : "No recorded value"); }
function sourceIds(values) { const shown = (values || []).slice(0, 20); return shown.join("; ") + ((values || []).length > shown.length ? `; +${values.length - shown.length} more` : ""); }
function wrapLines(value, limit) { const characters = Array.from(String(value || "")); const lines = []; while (characters.length && lines.length < 3) lines.push(characters.splice(0, limit).join("")); return lines.length ? lines : [""]; }
function drawText(svg, value, x, y, limit, attributes = {}) { const lines = wrapLines(value, limit); const text = svgElement("text", { x, y:y - Math.max(0, lines.length - 1) * 8, "text-anchor":"middle", "font-size":12, fill:"#18212b", ...attributes }); lines.forEach((line, index) => { const span = svgElement("tspan", { x, dy:index ? 15 : 0 }); span.textContent = line; text.appendChild(span); }); svg.appendChild(text); }
function edge(svg, parent, child, markerId) { svg.appendChild(svgElement("line", { x1:parent.x + parent.right, y1:parent.y, x2:child.x - child.left, y2:child.y, stroke:"#536670", "stroke-width":1.35, "marker-end":`url(#${markerId})` })); }
function nodeShape(svg, node) { const group = svgElement("g"); const title = svgElement("title"); title.textContent = `${node.node_type}: ${displayText(node)}\nSource scenarios: ${sourceIds(node.source_scenario_ids)}\nEvidence tickets: ${sourceIds(node.evidence_ticket_ids)}`; group.appendChild(title); if (node.node_type === "Entry") group.appendChild(svgElement("ellipse", { cx:node.x, cy:node.y, rx:node.left, ry:46, fill:"#d7ead1", stroke:"#55716d", "stroke-width":1.4 })); else if (node.node_type === "Issue Group") group.appendChild(svgElement("polygon", { points:`${node.x},${node.y - 56} ${node.x + node.left},${node.y} ${node.x},${node.y + 56} ${node.x - node.left},${node.y}`, fill:"#b8d7a9", stroke:"#55716d", "stroke-width":1.4 })); else group.appendChild(svgElement("rect", { x:node.x - node.left, y:node.y - 49, width:node.left * 2, height:98, rx:6, fill:node.node_type === "Trigger Pattern" ? "#fff0c6" : node.node_type === "Failure Pattern" ? "#d5e7f5" : "#e4e9ec", stroke:"#55716d", "stroke-width":1.4 })); svg.appendChild(group); drawText(svg, node.node_type, node.x, node.y - 29, 28, { fill:"#52616b", "font-size":10, "font-weight":700 }); drawText(svg, displayText(node), node.x, node.y + 4, node.node_type === "Ticket Solution" ? 29 : 27); drawText(svg, `${node.source_scenario_count} scenarios / ${node.source_ticket_count} tickets`, node.x, node.y + 34, 28, { fill:"#52616b", "font-size":10 }); }
function selectedBranch() { const root = rootNode(activeTree); const issue = nodeById(activeTree, issueSelector.value); const trigger = nodeById(activeTree, triggerSelector.value); if (!root || !issue || !trigger) return { root, issue, trigger, failures:[], solutions:[] }; const failures = childrenOf(activeTree, trigger.node_id, "Failure Pattern"); const solutions = failures.flatMap(failure => childrenOf(activeTree, failure.node_id, "Ticket Solution")); return { root, issue, trigger, failures, solutions }; }
function layoutFailurePatternTree() { const svg = document.getElementById("decision-tree"); svg.replaceChildren(); const branch = selectedBranch(); if (!branch.root || !branch.issue || !branch.trigger) { svg.setAttribute("viewBox", "0 0 1320 300"); drawText(svg, activeTree.service_function_label, 220, 130, 30); drawText(svg, "No mapped failure-pattern branch", 800, 130, 44, { fill:"#52616b" }); return; } const failureRows = Math.max(1, branch.failures.length); const height = Math.max(420, 150 + failureRows * 145); svg.setAttribute("viewBox", `0 0 1320 ${height}`); const markerId = "failure-pattern-arrow"; const defs = svgElement("defs"); const marker = svgElement("marker", { id:markerId, markerWidth:8, markerHeight:8, refX:7, refY:4, orient:"auto", markerUnits:"strokeWidth" }); marker.appendChild(svgElement("path", { d:"M 0 0 L 8 4 L 0 8 z", fill:"#536670" })); defs.appendChild(marker); svg.appendChild(defs); const anchorY = 120; const root = { ...branch.root, x:110, y:anchorY, left:88, right:88 }; const issue = { ...branch.issue, x:340, y:anchorY, left:125, right:125 }; const trigger = { ...branch.trigger, x:620, y:anchorY, left:124, right:124 }; const failurePositions = new Map(); const solutionPositions = new Map(); branch.failures.forEach((failure, index) => { const y = anchorY + index * 145; failurePositions.set(failure.node_id, { ...failure, x:900, y, left:132, right:132 }); const children = childrenOf(activeTree, failure.node_id, "Ticket Solution"); children.forEach((solution, solutionIndex) => solutionPositions.set(solution.node_id, { ...solution, x:1180, y:y + (solutionIndex - (children.length - 1) / 2) * 68, left:128, right:128 })); }); edge(svg, root, issue, markerId); edge(svg, issue, trigger, markerId); branch.failures.forEach(failure => edge(svg, trigger, failurePositions.get(failure.node_id), markerId)); branch.solutions.forEach(solution => { const parent = failurePositions.get(solution.parent_node_id); const child = solutionPositions.get(solution.node_id); if (parent && child) edge(svg, parent, child, markerId); }); [root, issue, trigger].forEach(node => nodeShape(svg, node)); branch.failures.forEach(failure => nodeShape(svg, failurePositions.get(failure.node_id))); branch.solutions.forEach(solution => nodeShape(svg, solutionPositions.get(solution.node_id))); }
function renderRows() { const body = document.getElementById("node-rows"); body.replaceChildren(); const branch = selectedBranch(); [branch.root, branch.issue, branch.trigger, ...branch.failures, ...branch.solutions].filter(Boolean).forEach(node => { const row = document.createElement("tr"); [node.node_id, node.parent_node_id, node.node_type, displayText(node), node.evidence_source, node.source_scenario_count, node.source_ticket_count, sourceIds(node.source_scenario_ids), sourceIds(node.evidence_ticket_ids), node.solution_status].forEach(value => { const cell = document.createElement("td"); cell.textContent = value || ""; row.appendChild(cell); }); body.appendChild(row); }); document.getElementById("failure-count").textContent = String(branch.failures.length); document.getElementById("solution-count").textContent = String(branch.solutions.length); }
function populateIssueSelector(tree) { issueSelector.replaceChildren(); const root = rootNode(tree); const issues = root ? childrenOf(tree, root.node_id, "Issue Group") : []; if (!issues.length) { const option = document.createElement("option"); option.value = ""; option.textContent = "No mapped issue group"; issueSelector.appendChild(option); issueSelector.disabled = true; return; } issueSelector.disabled = false; issues.forEach(issue => { const option = document.createElement("option"); option.value = issue.node_id; option.textContent = `${displayText(issue)} (${issue.source_scenario_count} scenarios / ${issue.source_ticket_count} tickets)`; issueSelector.appendChild(option); }); }
function populateTriggerSelector(tree) { triggerSelector.replaceChildren(); const triggers = issueSelector.value ? childrenOf(tree, issueSelector.value, "Trigger Pattern") : []; if (!triggers.length) { const option = document.createElement("option"); option.value = ""; option.textContent = "No mapped trigger pattern"; triggerSelector.appendChild(option); triggerSelector.disabled = true; return; } triggerSelector.disabled = false; triggers.forEach(trigger => { const option = document.createElement("option"); option.value = trigger.node_id; option.textContent = `${displayText(trigger)} (${trigger.source_scenario_count} scenarios / ${trigger.source_ticket_count} tickets)`; triggerSelector.appendChild(option); }); }
function renderActiveBranch() { layoutFailurePatternTree(); renderRows(); }
function selectTree(treeId) { activeTree = trees.find(tree => tree.tree_id === treeId) || trees[0] || null; if (!activeTree) return; document.getElementById("tree-title").textContent = activeTree.service_function_label; document.getElementById("scenario-count").textContent = String(activeTree.scenario_count); document.getElementById("ticket-count").textContent = String(activeTree.ticket_count); document.getElementById("source-status").textContent = activeTree.source_status; populateIssueSelector(activeTree); populateTriggerSelector(activeTree); renderActiveBranch(); }
trees.forEach(tree => { const option = document.createElement("option"); option.value = tree.tree_id; option.textContent = `${tree.service_function_label} (${tree.scenario_count} scenarios / ${tree.ticket_count} tickets)`; treeSelector.appendChild(option); }); const defaultTree = trees.find(tree => tree.service_function_id === "CARPLAY" && tree.scenario_count) || trees.find(tree => tree.scenario_count) || trees[0]; if (defaultTree) { treeSelector.value = defaultTree.tree_id; selectTree(defaultTree.tree_id); } treeSelector.addEventListener("change", event => selectTree(event.target.value)); issueSelector.addEventListener("change", () => { populateTriggerSelector(activeTree); renderActiveBranch(); }); triggerSelector.addEventListener("change", renderActiveBranch);
</script>
</body>
</html>"""
        return template.replace("__TREE_DATA__", serialized_trees)


def _header_positions(worksheet: Any) -> dict[str, int]:
    return {
        _text(cell.value): index
        for index, cell in enumerate(
            next(worksheet.iter_rows(min_row=1, max_row=1))
        )
        if _text(cell.value)
    }


def _required_positions(
    worksheet: Any, required_headers: tuple[str, ...], source_name: str
) -> dict[str, int]:
    positions = _header_positions(worksheet)
    missing = [header for header in required_headers if header not in positions]
    if missing:
        raise ValueError(f"Missing {', '.join(missing)} in {source_name}")
    return positions


def _read_service_function_ids(knowledge_base_path: Path) -> dict[str, str]:
    with knowledge_base_path.open(encoding="utf-8-sig") as source:
        payload = json.load(source)
    return {
        _text(tree.get("service_function")): _text(tree.get("service_function_id"))
        for tree in payload.get("decision_trees", [])
        if isinstance(tree, dict)
        and _text(tree.get("service_function"))
        and _text(tree.get("service_function_id"))
    }


def load_v12_scenarios(
    scenario_workbook_path: Path, knowledge_base_path: Path
) -> list[dict[str, Any]]:
    """Load V12 Scenario and Ticket mappings without changing the source workbook."""
    service_function_ids = _read_service_function_ids(knowledge_base_path)
    workbook = openpyxl.load_workbook(
        scenario_workbook_path, read_only=True, data_only=True
    )
    try:
        scenario_sheet = workbook["01_Scenario_Library"]
        scenario_positions = _required_positions(
            scenario_sheet,
            (
                "Service Function",
                "Scenario ID",
                "Problem Type",
                "Customer Symptom",
                "Trigger Condition",
                "Ticket Count",
                "Recorded Recovery Action",
                "Evidence Confidence",
                "Rep Ticket ID",
            ),
            "01_Scenario_Library",
        )
        scenarios_by_id: dict[str, dict[str, Any]] = {}
        for row in scenario_sheet.iter_rows(min_row=2, values_only=True):
            scenario_id = _text(row[scenario_positions["Scenario ID"]])
            if not scenario_id:
                continue
            service_function = _text(row[scenario_positions["Service Function"]])
            service_function_id = service_function_ids.get(service_function)
            if not service_function_id:
                raise ValueError(
                    f"No Service Function ID in knowledge base for {service_function!r}"
                )
            scenarios_by_id[scenario_id] = {
                "service_function_id": service_function_id,
                "service_function": service_function,
                "scenario_id": scenario_id,
                "problem_type": _text(row[scenario_positions["Problem Type"]]),
                "customer_symptom": _text(
                    row[scenario_positions["Customer Symptom"]]
                ),
                "trigger_condition": _text(
                    row[scenario_positions["Trigger Condition"]]
                ),
                "ticket_count": int(row[scenario_positions["Ticket Count"]] or 0),
                "recovery_action": _text(
                    row[scenario_positions["Recorded Recovery Action"]]
                ),
                "evidence_confidence": _text(
                    row[scenario_positions["Evidence Confidence"]]
                ),
                "rep_ticket_id": _text(
                    row[scenario_positions["Rep Ticket ID"]]
                ),
                "rep_title": _text(
                    row[scenario_positions["Rep Title"]]
                ) if "Rep Title" in scenario_positions else "",
                "observed_behaviour": _text(
                    row[scenario_positions["Observed Behaviour"]]
                ) if "Observed Behaviour" in scenario_positions else "",
                "ticket_ids": [],
            }

        mapping_sheet = workbook["07_Ticket_Mapping"]
        mapping_positions = _required_positions(
            mapping_sheet,
            ("Ticket ID", "Scenario ID"),
            "07_Ticket_Mapping",
        )
        ticket_to_scenario: dict[str, str] = {}
        for row in mapping_sheet.iter_rows(min_row=2, values_only=True):
            ticket_id = _text(row[mapping_positions["Ticket ID"]])
            scenario_id = _text(row[mapping_positions["Scenario ID"]])
            if not scenario_id:
                continue
            if scenario_id not in scenarios_by_id:
                raise ValueError(
                    f"Ticket mapping references unknown Scenario {scenario_id}"
                )
            if not ticket_id:
                raise ValueError(
                    f"Ticket mapping is missing Ticket ID for Scenario {scenario_id}"
                )
            previous_scenario_id = ticket_to_scenario.get(ticket_id)
            if previous_scenario_id:
                raise ValueError(
                    f"Duplicate Ticket mapping for {ticket_id}: "
                    f"{previous_scenario_id}, {scenario_id}"
                )
            ticket_to_scenario[ticket_id] = scenario_id
            scenarios_by_id[scenario_id]["ticket_ids"].append(ticket_id)

        for scenario in scenarios_by_id.values():
            declared_ticket_count = int(scenario["ticket_count"] or 0)
            mapped_ticket_count = len(scenario["ticket_ids"])
            if mapped_ticket_count != declared_ticket_count:
                raise ValueError(
                    "Ticket mapping count mismatch for Scenario "
                    f"{scenario['scenario_id']}: declared {declared_ticket_count}, "
                    f"mapped {mapped_ticket_count}"
                )
        return list(scenarios_by_id.values())
    finally:
        workbook.close()


def load_ticket_evidence(source_workbook_path: Path) -> dict[str, dict[str, Any]]:
    """Load raw RCA candidate text; this function makes no causal inference."""
    workbook = openpyxl.load_workbook(
        source_workbook_path, read_only=True, data_only=True
    )
    try:
        worksheet = workbook["05_全部明细"]
        positions = _required_positions(
            worksheet,
            ("Ticket ID", "RCA Root Cause"),
            "05_全部明细",
        )
        evidence = {}
        for row in worksheet.iter_rows(min_row=2, values_only=True):
            ticket_id = _text(row[positions["Ticket ID"]])
            if ticket_id:
                evidence[ticket_id] = {
                    "rca_root_cause": _text(row[positions["RCA Root Cause"]]),
                }
        return evidence
    finally:
        workbook.close()


def _style_header(worksheet: Any, column_count: int, row: int = 1) -> None:
    fill = PatternFill("solid", fgColor="1F4E78")
    font = Font(color="FFFFFF", bold=True)
    border = Border(bottom=Side(style="thin", color="9EADBE"))
    for column in range(1, column_count + 1):
        cell = worksheet.cell(row=row, column=column)
        cell.fill = fill
        cell.font = font
        cell.border = border
        cell.alignment = Alignment(vertical="center", wrap_text=True)


def _format_sheet(worksheet: Any, widths: list[int], freeze: str = "A2") -> None:
    worksheet.freeze_panes = freeze
    worksheet.auto_filter.ref = worksheet.dimensions
    for index, width in enumerate(widths, start=1):
        worksheet.column_dimensions[get_column_letter(index)].width = width


def _ratio(value: float | None) -> str:
    return "" if value is None else f"{value:.1%}"


def _display_ids(values: list[str], limit: int = 20) -> str:
    displayed_values = values[:limit]
    suffix = f"; +{len(values) - limit} more" if len(values) > limit else ""
    return "; ".join(displayed_values) + suffix


def write_failure_pattern_graph_workbook(graph: dict[str, Any], output_path: Path) -> None:
    """Write the graph into review sheets and make evidence limits visible."""
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    patterns = graph["failure_patterns"]
    candidates = graph["failure_mode_candidates"]
    actions = graph["action_records"]
    failure_pattern_trees = graph.get("failure_pattern_trees")
    failure_pattern_tree_nodes = graph.get("failure_pattern_tree_nodes")
    if not isinstance(failure_pattern_trees, list) or not isinstance(
        failure_pattern_tree_nodes, list
    ):
        failure_pattern_trees, failure_pattern_tree_nodes = build_failure_pattern_trees(
            graph
        )

    overview = workbook.create_sheet("00_Overview")
    overview.append(["IDCEVO Phase 09 V13 Failure Pattern Graph"])
    overview.append([])
    overview.append(["Failure Pattern Tree Path", graph["model_path"]])
    overview.append(["RCA Audit Overlay Path", graph["audit_graph_path"]])
    overview.append(["Scenario-to-pattern records", len(patterns)])
    overview.append(["Recorded failure-mode candidates", len(candidates)])
    overview.append([
        "Patterns with causal evidence",
        sum(1 for record in patterns if record["causal_evidence_ticket_count"]),
    ])
    overview.append([
        "Patterns with ticket-recorded recovery",
        sum(1 for record in patterns if record["recorded_recovery_action"]),
    ])
    overview.append([])
    overview.append(["Governance Rule", "Meaning"])
    for key, value in graph["governance"].items():
        overview.append([key, value])
    _style_header(overview, 2, row=10)
    overview.cell(row=1, column=1).font = Font(bold=True, size=14)
    _format_sheet(overview, [38, 112], freeze="A11")

    pattern_sheet = workbook.create_sheet("01_Failure_Pattern_Graph")
    pattern_headers = [
        "Service Function",
        "Scenario ID",
        "Problem Type",
        "Customer Symptom",
        "Trigger Condition",
        "Failure Pattern ID",
        "Failure Pattern",
        "Source Ticket Count",
        "Observed Frequency Within Service Function",
        "RCA Evidence Ticket Count",
        "RCA Evidence Coverage",
        "Recorded Failure Mode Candidate",
        "Candidate Support",
        "Failure Mode Status",
        "Cause Probability Status",
        "Recorded Recovery Action",
        "Action Status",
        "Evidence Confidence",
    ]
    pattern_sheet.append(pattern_headers)
    for record in patterns:
        pattern_sheet.append([
            record["service_function"],
            record["scenario_id"],
            record["problem_type"],
            record["customer_symptom"],
            record["trigger_condition"],
            record["failure_pattern_id"],
            record["failure_pattern"],
            record["source_ticket_count"],
            _ratio(record["observed_frequency_ratio"]),
            record["causal_evidence_ticket_count"],
            _ratio(record["causal_evidence_coverage_ratio"]),
            record["failure_mode_candidate"],
            _ratio(record["candidate_support_ratio"]),
            record["failure_mode_status"],
            record["cause_probability_status"],
            record["recorded_recovery_action"],
            record["action_status"],
            record["evidence_confidence"],
        ])
    _style_header(pattern_sheet, len(pattern_headers))
    _format_sheet(
        pattern_sheet,
        [28, 15, 34, 42, 42, 20, 52, 16, 20, 18, 18, 58, 18, 44, 24, 58, 42, 18],
        freeze="F2",
    )

    candidate_sheet = workbook.create_sheet("02_Failure_Mode_Candidates")
    candidate_headers = [
        "Service Function",
        "Scenario ID",
        "Failure Pattern ID",
        "Failure Pattern",
        "Recorded Failure Mode Candidate",
        "Candidate Ticket Count",
        "Pattern Ticket Count",
        "Candidate Support",
        "Review Status",
        "Cause Probability Status",
        "Source Ticket IDs",
        "Evidence Source",
    ]
    candidate_sheet.append(candidate_headers)
    for candidate in candidates:
        candidate_sheet.append([
            candidate["service_function"],
            candidate["scenario_id"],
            candidate["failure_pattern_id"],
            candidate["failure_pattern"],
            candidate["failure_mode_candidate"],
            candidate["candidate_ticket_count"],
            candidate["source_ticket_count"],
            _ratio(candidate["candidate_support_ratio"]),
            candidate["review_status"],
            candidate["cause_probability_status"],
            "; ".join(candidate.get("source_ticket_ids", [])),
            candidate["evidence_source"],
        ])
    _style_header(candidate_sheet, len(candidate_headers))
    _format_sheet(
        candidate_sheet,
        [28, 15, 20, 52, 72, 18, 18, 18, 22, 24, 42, 38],
        freeze="E2",
    )

    action_sheet = workbook.create_sheet("03_Action_Catalog")
    action_headers = [
        "Service Function",
        "Scenario ID",
        "Failure Pattern ID",
        "Failure Pattern",
        "Recorded Recovery Action",
        "Action Status",
        "Source Ticket Count",
        "Source Ticket IDs",
        "Evidence Source",
        "Provenance Status",
    ]
    action_sheet.append(action_headers)
    for action in actions:
        action_sheet.append([
            action["service_function"],
            action["scenario_id"],
            action["failure_pattern_id"],
            action["failure_pattern"],
            action["recorded_recovery_action"],
            action["action_status"],
            action["source_ticket_count"],
            "; ".join(action.get("source_ticket_ids", [])),
            action.get("evidence_source", ""),
            action.get("provenance_status", ""),
        ])
    _style_header(action_sheet, len(action_headers))
    _format_sheet(
        action_sheet,
        [28, 15, 20, 52, 72, 42, 18, 42, 48, 36],
        freeze="E2",
    )

    node_sheet = workbook.create_sheet("04_Graph_Nodes")
    node_headers = [
        "Node ID",
        "Node Type",
        "Label",
        "Source Scenario Count",
        "Source Ticket Count",
        "Attributes JSON",
    ]
    node_sheet.append(node_headers)
    for node in graph["nodes"]:
        node_sheet.append([
            node["node_id"],
            node["node_type"],
            node["label"],
            node["source_scenario_count"],
            node["source_ticket_count"],
            json.dumps(node["attributes"], ensure_ascii=False, sort_keys=True),
        ])
    _style_header(node_sheet, len(node_headers))
    _format_sheet(node_sheet, [24, 40, 78, 22, 20, 88], freeze="C2")

    edge_sheet = workbook.create_sheet("05_Graph_Edges")
    edge_headers = [
        "Edge ID",
        "Source Node ID",
        "Target Node ID",
        "Relation Type",
        "Evidence Status",
        "Source Scenario Count",
        "Source Ticket Count",
        "Support Ratio",
        "Source Ticket IDs",
    ]
    edge_sheet.append(edge_headers)
    for edge in graph["edges"]:
        edge_sheet.append([
            edge["edge_id"],
            edge["source_node_id"],
            edge["target_node_id"],
            edge["relation_type"],
            edge["evidence_status"],
            edge["source_scenario_count"],
            edge["source_ticket_count"],
            _ratio(edge["support_ratio"]),
            "; ".join(edge.get("source_ticket_ids", [])),
        ])
    _style_header(edge_sheet, len(edge_headers))
    _format_sheet(
        edge_sheet,
        [24, 24, 24, 42, 48, 22, 20, 18, 42],
        freeze="D2",
    )

    dictionary_sheet = workbook.create_sheet("06_Data_Dictionary")
    dictionary_sheet.append(["Field", "Definition", "Governance"])
    for row in [
        (
            "Failure Pattern",
            "Observable pattern derived from V12 Problem Type, Symptom, and Trigger.",
            "It is not a technical root cause.",
        ),
        (
            "Recorded Failure Mode Candidate",
            "Raw RCA Root Cause text linked from source Tickets.",
            "Always review_required; it is not a confirmed failure mode.",
        ),
        (
            "Candidate Support",
            "Tickets with the same recorded RCA candidate divided by Scenario Tickets.",
            "Descriptive evidence support only, not causal probability.",
        ),
        (
            "Observed Frequency Within Service Function",
            "Scenario Tickets divided by all V12 Scenario Tickets in that Service Function.",
            "Historical observation frequency, not field incidence.",
        ),
        (
            "Cause Probability Status",
            "Causal probability publication state.",
            "not_estimated until an approved causal model is supplied.",
        ),
        (
            "Recorded Recovery Action",
            "Recovery Action copied from V12 Scenario evidence.",
            "May be a workaround; it is not an approved repair instruction.",
        ),
    ]:
        dictionary_sheet.append(list(row))
    _style_header(dictionary_sheet, 3)
    _format_sheet(dictionary_sheet, [42, 88, 88], freeze="A2")

    tree_catalog_sheet = workbook.create_sheet("07_Failure_Pattern_Tree_Catalog")
    tree_catalog_headers = [
        "Service Function",
        "Catalog Status",
        "Scenario Count",
        "Ticket Count",
        "Issue Group Count",
        "Trigger Pattern Count",
        "Failure Pattern Count",
        "Node Count",
        "Edge Count",
        "Diagnostic Layers",
        "Solution Source Policy",
        "Tree Generation",
        "Source Status",
    ]
    tree_catalog_sheet.append(tree_catalog_headers)
    for tree in failure_pattern_trees:
        tree_catalog_sheet.append([
            tree["service_function"],
            tree["catalog_status"],
            tree["scenario_count"],
            tree["ticket_count"],
            tree["issue_group_count"],
            tree["trigger_pattern_count"],
            tree["failure_pattern_count"],
            tree["node_count"],
            tree["edge_count"],
            "; ".join(tree["diagnostic_layers"]),
            tree["solution_source_policy"],
            tree["tree_generation"],
            tree["source_status"],
        ])
    _style_header(tree_catalog_sheet, len(tree_catalog_headers))
    _format_sheet(
        tree_catalog_sheet,
        [30, 28, 16, 16, 18, 20, 20, 14, 14, 68, 88, 34, 86],
        freeze="C2",
    )

    tree_node_sheet = workbook.create_sheet("08_Failure_Pattern_Tree_Nodes")
    tree_node_headers = [
        "Service Function",
        "Node ID",
        "Parent Node ID",
        "Node Type",
        "Data Key",
        "Question / Solution",
        "Evidence Source",
        "Source Scenario Count",
        "Source Ticket Count",
        "Source Scenario IDs",
        "Source Ticket IDs",
        "Evidence Ticket IDs",
        "Solution Status",
        "Node Source",
    ]
    tree_node_sheet.append(tree_node_headers)
    service_function_by_tree_id = {
        tree["tree_id"]: tree["service_function"]
        for tree in failure_pattern_trees
    }
    for node in failure_pattern_tree_nodes:
        tree_node_sheet.append([
            service_function_by_tree_id[node["tree_id"]],
            node["node_id"],
            node["parent_node_id"],
            node["node_type"],
            node["data_key"],
            node["question_or_action"],
            node["evidence_source"],
            node["source_scenario_count"],
            node["source_ticket_count"],
            _display_ids(node["source_scenario_ids"]),
            _display_ids(node["source_ticket_ids"]),
            _display_ids(node["evidence_ticket_ids"]),
            node["solution_status"],
            node["node_source"],
        ])
    _style_header(tree_node_sheet, len(tree_node_headers))
    _format_sheet(
        tree_node_sheet,
        [30, 24, 24, 24, 38, 74, 48, 20, 18, 52, 52, 52, 34, 34],
        freeze="D2",
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)


def build_output_paths(output_xlsx: Path) -> tuple[Path, Path]:
    return (
        output_xlsx.with_suffix(".json"),
        output_xlsx.with_name(f"{output_xlsx.stem}_Catalog.html"),
    )


def _render_failure_pattern_record_list_html(graph: dict[str, Any]) -> str:
    """Render a compact offline review catalog from graph records."""
    data = _serialized_html_data(graph["failure_patterns"])
    overflow_protection_css = (
        ".records,article,article *,h2,.route,dd{min-width:0;overflow-wrap:anywhere;}"
    )
    return f"""<!doctype html>
<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>Phase 09 Failure Pattern Graph</title>
<style>{overflow_protection_css}
:root {{ --ink:#17212b; --muted:#607080; --line:#cbd5df; --paper:#f7f6f2; --navy:#143d59; --teal:#0f766e; --amber:#b45309; --white:#fff; }}
* {{ box-sizing:border-box; }} body {{ margin:0; color:var(--ink); background:var(--paper); font-family:Georgia,'Noto Serif SC',serif; }}
header {{ background:var(--navy); color:var(--white); padding:32px max(24px,calc((100vw - 1320px) / 2)); }} h1 {{ margin:0; font-size:28px; font-weight:600; letter-spacing:0; }} header p {{ max-width:900px; margin:10px 0 0; color:#dbe9f3; line-height:1.55; }}
main {{ max-width:1320px; padding:24px; margin:auto; }} .legend {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:1px; background:var(--line); border:1px solid var(--line); }} .legend div {{ padding:15px; background:var(--white); font-size:14px; }} .legend strong {{ display:block; color:var(--navy); margin-bottom:5px; }}
.toolbar {{ display:flex; gap:12px; margin:22px 0 12px; flex-wrap:wrap; }} input,select {{ min-height:38px; border:1px solid var(--line); background:var(--white); padding:8px 10px; color:var(--ink); font:inherit; }} input {{ flex:1 1 360px; }} select {{ flex:0 1 260px; }} .count {{ color:var(--muted); font-size:14px; align-self:center; }} .records {{ display:grid; gap:10px; }} article {{ background:var(--white); border:1px solid var(--line); border-left:5px solid var(--teal); padding:16px; }} .route {{ color:var(--teal); font-weight:700; font-size:13px; }} h2 {{ font-size:18px; margin:8px 0; font-weight:600; }} dl {{ display:grid; grid-template-columns:190px 1fr; margin:0; gap:5px 16px; font-size:14px; line-height:1.45; }} dt {{ color:var(--muted); }} dd {{ margin:0; }} .warn {{ color:var(--amber); font-weight:700; }} @media(max-width:680px) {{ .legend {{ grid-template-columns:1fr; }} dl {{ grid-template-columns:1fr; gap:2px; }} dd {{ margin-bottom:9px; }} }}
</style></head><body><header><h1>Phase 09 Failure Pattern Graph</h1><p>Service Function -> Scenario -> Failure Pattern -> recorded causal candidate or evidence gap -> recorded recovery. Candidate support is not causal probability, and ticket recovery is not an approved repair.</p></header><main><section class=\"legend\"><div><strong>Failure Pattern</strong>Observed symptom and trigger cluster.</div><div><strong>Failure Mode Candidate</strong>Raw RCA text, review required.</div><div><strong>Support</strong>Historical evidence coverage only.</div><div><strong>Action</strong>Ticket-recorded recovery only.</div></section><div class=\"toolbar\"><input id=\"query\" placeholder=\"Search service, symptom, trigger, candidate, or action\"><select id=\"service\"><option value=\"\">All service functions</option></select><span id=\"count\" class=\"count\"></span></div><section id=\"records\" class=\"records\"></section></main><script>
const records={data};const query=document.getElementById('query');const service=document.getElementById('service');const count=document.getElementById('count');const root=document.getElementById('records');const esc=value=>String(value||'').replace(/[&<>\"]/g,char=>({{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}}[char]));for(const name of [...new Set(records.map(record=>record.service_function))].sort())service.insertAdjacentHTML('beforeend',`<option>${{esc(name)}}</option>`);const ratio=value=>`${{(Number(value||0)*100).toFixed(1)}}%`;function render(){{const terms=query.value.trim().toLowerCase();const selected=service.value;const filtered=records.filter(record=>{{const searchable=Object.values(record).join(' ').toLowerCase();return(!selected||record.service_function===selected)&&(!terms||searchable.includes(terms));}});count.textContent=`${{filtered.length.toLocaleString()}} of ${{records.length.toLocaleString()}} scenario-pattern records`;root.innerHTML=filtered.slice(0,500).map(record=>`<article><div class=\"route\">${{esc(record.service_function)}} / ${{esc(record.scenario_id)}} / ${{esc(record.failure_pattern_id)}}</div><h2>${{esc(record.failure_pattern)}}</h2><dl><dt>Customer symptom</dt><dd>${{esc(record.customer_symptom)}}</dd><dt>Trigger condition</dt><dd>${{esc(record.trigger_condition)}}</dd><dt>Recorded candidate</dt><dd class=\"${{record.failure_mode_candidate?'warn':''}}\">${{esc(record.failure_mode_candidate||'No recorded causal evidence')}}</dd><dt>Evidence coverage</dt><dd>${{esc(record.causal_evidence_ticket_count)}} / ${{esc(record.source_ticket_count)}} tickets (${{ratio(record.causal_evidence_coverage_ratio)}})</dd><dt>Candidate support</dt><dd>${{ratio(record.candidate_support_ratio)}}; causal probability: ${{esc(record.cause_probability_status)}}</dd><dt>Recorded recovery</dt><dd>${{esc(record.recorded_recovery_action||'No ticket-recorded recovery')}}</dd></dl></article>`).join('')||'<article>No matching records.</article>';if(filtered.length>500)root.insertAdjacentHTML('beforeend','<p class=\"count\">Only the first 500 matching records are rendered. Narrow the filter to inspect more.</p>');}}query.addEventListener('input',render);service.addEventListener('change',render);render();
</script></body></html>"""


def render_failure_pattern_catalog_html(graph: dict[str, Any]) -> str:
    """Render the default V13 catalog as V12-style Failure Pattern trees."""
    return render_failure_pattern_tree_catalog_html(graph)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a V13 Failure Pattern Graph from a V12 scenario workbook."
    )
    parser.add_argument(
        "--scenario-workbook",
        type=Path,
        default=DEFAULT_SCENARIO_WORKBOOK,
        help="V12 Scenario Library workbook.",
    )
    parser.add_argument(
        "--knowledge-base",
        type=Path,
        help="Matching V12 Decision Tree Knowledge Base JSON. Defaults beside the workbook.",
    )
    parser.add_argument(
        "--source-workbook",
        type=Path,
        default=DEFAULT_SOURCE_WORKBOOK,
        help="Phase 09 Closure Risk workbook with RCA Root Cause evidence.",
    )
    parser.add_argument(
        "--output-xlsx",
        type=Path,
        help="V13 workbook. Defaults beside the V12 input.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    scenario_workbook = args.scenario_workbook.resolve()
    source_workbook = args.source_workbook.resolve()
    knowledge_base = args.knowledge_base or scenario_workbook.with_name(
        f"{scenario_workbook.stem}_Decision_Tree_Knowledge_Base.json"
    )
    knowledge_base = knowledge_base.resolve()
    output_xlsx = args.output_xlsx or scenario_workbook.with_name(
        "IDCEVO_Phase09_Field_Escape_Scenario_Library_V13_Failure_Pattern_Graph.xlsx"
    )
    output_xlsx = output_xlsx.resolve()
    for path, description in (
        (scenario_workbook, "Scenario workbook"),
        (knowledge_base, "Knowledge base"),
        (source_workbook, "Source workbook"),
    ):
        if not path.is_file():
            raise FileNotFoundError(f"{description} not found: {path}")

    graph = build_failure_pattern_graph(
        load_v12_scenarios(scenario_workbook, knowledge_base),
        load_ticket_evidence(source_workbook),
    )
    failure_pattern_trees, failure_pattern_tree_nodes = build_failure_pattern_trees(
        graph
    )
    graph["failure_pattern_trees"] = failure_pattern_trees
    graph["failure_pattern_tree_nodes"] = failure_pattern_tree_nodes
    output_json, output_html = build_output_paths(output_xlsx)
    write_failure_pattern_graph_workbook(graph, output_xlsx)
    output_json.write_text(
        json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    output_html.write_text(render_failure_pattern_catalog_html(graph), encoding="utf-8")
    print(f"Scenarios: {len(graph['failure_patterns'])}")
    print(f"Recorded failure-mode candidates: {len(graph['failure_mode_candidates'])}")
    print(f"Excel: {output_xlsx}")
    print(f"JSON: {output_json}")
    print(f"Catalog: {output_html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())