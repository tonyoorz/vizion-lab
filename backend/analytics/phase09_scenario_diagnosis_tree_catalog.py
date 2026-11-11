#!/usr/bin/env python3
"""Build all-function, scenario-level after-sales diagnosis trees.

Every V12 Scenario ID becomes a continuous diagnostic path rather than a
statistical symptom/trigger aggregate. Source-tree experience, ticket-recorded
RCA text, and generic evidence collection remain separate evidence classes.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import sys
from typing import Any

from openpyxl import load_workbook

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.analytics.phase09_all_function_after_sales_catalog import (
    _load_source_graphs,
)


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
DEFAULT_SOURCE_WORKBOOK = (
    PHASE09_EXPORT_DIR
    / "IDCEVO_Phase09_Closure_Risk_20260908_FullLLM_Complete.xlsx"
)
DEFAULT_REGISTRY = SOURCE_TREE_DIR / "tree_registry.json"
DEFAULT_MANIFEST = SOURCE_TREE_DIR / "lossless_webp" / "manifest.json"
DEFAULT_EXTRACTED_DIRECTORY = SOURCE_TREE_DIR / "extracted"
DEFAULT_OUTPUT_HTML = (
    PHASE09_EXPORT_DIR
    / "IDCEVO_Phase09_All_Function_Scenario_Diagnosis_Tree_Catalog_V2.html"
)
DEFAULT_OUTPUT_JSON = (
    PHASE09_EXPORT_DIR
    / "IDCEVO_Phase09_All_Function_Scenario_Diagnosis_Tree_Catalog_V2.json"
)

_EMPTY_VALUES = {
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
_RCA_NOISE_MARKERS = (
    "triggerreason",
    "octane",
    "attachment",
    "according to the official",
    "componentinfo",
    "callsmanager:",
    "log info",
    "code = success",
    "defect ",
    "user",
    "bootmode",
    "debuginfo",
    "ext2",
    "please check if",
    "reproducible on the newest",
    "for rejecting this ticket",
    "fix defined",
    "workaround defined",
    "candidate available",
    "available back from",
)
_SOURCE_MATCH_STRONG = 0.32
_SOURCE_MATCH_MEDIUM = 0.20
_MAX_SOURCE_EXPERIENCE_BRANCHES = 2


def _text(value: Any) -> str:
    return str(value or "").strip()


def _integer(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _load_json_object(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8-sig") as source:
        payload = json.load(source)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return payload


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


def _row_value(row: tuple[Any, ...], positions: dict[str, int], header: str) -> Any:
    position = positions.get(header)
    return row[position] if position is not None and position < len(row) else ""


def _optional_text(
    row: tuple[Any, ...], positions: dict[str, int], header: str
) -> str:
    return _text(_row_value(row, positions, header))


def _service_function_metadata(knowledge_base: dict[str, Any]) -> dict[str, dict[str, Any]]:
    metadata_by_service = {}
    for tree in knowledge_base.get("decision_trees", []):
        if not isinstance(tree, dict):
            continue
        service_function = _text(tree.get("service_function"))
        service_function_id = _text(tree.get("service_function_id"))
        if service_function and service_function_id:
            metadata_by_service[service_function] = {
                "tree_id": _text(tree.get("tree_id")) or f"DT-{service_function_id}",
                "service_function_id": service_function_id,
                "service_function": service_function,
                "service_function_label": _text(tree.get("service_function_label"))
                or service_function,
                "service_domain": _text(tree.get("service_domain")),
                "catalog_status": _text(tree.get("catalog_status")),
                "reference_tree_ids": list(tree.get("reference_tree_ids") or []),
            }
    return metadata_by_service


def load_v12_scenario_paths(
    scenario_workbook_path: Path, knowledge_base_path: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load current V12 scenarios and their exact ticket mappings."""
    knowledge_base = _load_json_object(knowledge_base_path)
    metadata_by_service = _service_function_metadata(knowledge_base)
    workbook = load_workbook(scenario_workbook_path, read_only=True, data_only=True)
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
            scenario_id = _optional_text(row, scenario_positions, "Scenario ID")
            if not scenario_id:
                continue
            service_function = _optional_text(
                row, scenario_positions, "Service Function"
            )
            metadata = metadata_by_service.get(service_function)
            if metadata is None:
                raise ValueError(
                    f"No service-function metadata for {service_function!r}"
                )
            scenarios_by_id[scenario_id] = {
                **metadata,
                "scenario_id": scenario_id,
                "problem_type": _optional_text(row, scenario_positions, "Problem Type"),
                "customer_symptom": _optional_text(
                    row, scenario_positions, "Customer Symptom"
                ),
                "trigger_condition": _optional_text(
                    row, scenario_positions, "Trigger Condition"
                ),
                "observed_behaviour": _optional_text(
                    row, scenario_positions, "Observed Behaviour"
                ),
                "expected_behaviour": _optional_text(
                    row, scenario_positions, "Expected Behaviour"
                ),
                "rep_title": _optional_text(row, scenario_positions, "Rep Title"),
                "rep_ticket_id": _optional_text(
                    row, scenario_positions, "Rep Ticket ID"
                ),
                "ticket_count": _integer(
                    _row_value(row, scenario_positions, "Ticket Count")
                ),
                "risk_band": _optional_text(row, scenario_positions, "Risk Band"),
                "risk_index": _row_value(row, scenario_positions, "Risk Index"),
                "recovery_action": _optional_text(
                    row, scenario_positions, "Recorded Recovery Action"
                ),
                "evidence_confidence": _optional_text(
                    row, scenario_positions, "Evidence Confidence"
                ),
                "ticket_ids": [],
            }

        mapping_sheet_name = next(
            (
                sheet_name
                for sheet_name in (
                    "07_Ticket_Mapping",
                    "06_Ticket_Mapping",
                    "05_Ticket_Mapping",
                )
                if sheet_name in workbook.sheetnames
            ),
            "",
        )
        if not mapping_sheet_name:
            raise ValueError(
                "Missing ticket mapping sheet; expected 07_Ticket_Mapping, "
                "06_Ticket_Mapping, or 05_Ticket_Mapping"
            )
        mapping_sheet = workbook[mapping_sheet_name]
        mapping_positions = _required_positions(
            mapping_sheet,
            ("Ticket ID", "Scenario ID"),
            mapping_sheet_name,
        )
        ticket_to_scenario: dict[str, str] = {}
        for row in mapping_sheet.iter_rows(min_row=2, values_only=True):
            ticket_id = _optional_text(row, mapping_positions, "Ticket ID")
            scenario_id = _optional_text(row, mapping_positions, "Scenario ID")
            if not scenario_id:
                continue
            if scenario_id not in scenarios_by_id:
                raise ValueError(
                    f"Ticket mapping references unknown Scenario {scenario_id}"
                )
            if not ticket_id:
                raise ValueError(
                    f"Ticket mapping missing Ticket ID for {scenario_id}"
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
            if len(scenario["ticket_ids"]) != scenario["ticket_count"]:
                raise ValueError(
                    "Ticket mapping count mismatch for Scenario "
                    f"{scenario['scenario_id']}: declared {scenario['ticket_count']}, "
                    f"mapped {len(scenario['ticket_ids'])}"
                )
        return list(scenarios_by_id.values()), list(metadata_by_service.values())
    finally:
        workbook.close()


def load_ticket_diagnostic_evidence(source_workbook_path: Path) -> dict[str, dict[str, str]]:
    """Load ticket fields used as traceable evidence, without causal inference."""
    workbook = load_workbook(source_workbook_path, read_only=True, data_only=True)
    try:
        worksheet = workbook["05_全部明细"]
        positions = _required_positions(worksheet, ("Ticket ID",), "05_全部明细")
        evidence_by_ticket_id = {}
        fields = (
            "RCA Root Cause",
            "Assigned ECU",
            "Software Version",
            "RCA Observed Behaviour",
            "RCA Recovery Action",
            "Ticket 标题",
            "问题描述摘录",
        )
        for row in worksheet.iter_rows(min_row=2, values_only=True):
            ticket_id = _optional_text(row, positions, "Ticket ID")
            if not ticket_id:
                continue
            evidence_by_ticket_id[ticket_id] = {
                field: _optional_text(row, positions, field) for field in fields
            }
        return evidence_by_ticket_id
    finally:
        workbook.close()


def _normalized_text(value: Any) -> str:
    return _text(value).casefold()


def _meaningful_text(value: Any) -> str:
    normalized = _normalized_text(value)
    return "" if normalized in _EMPTY_VALUES else _text(value)


def _usable_recorded_rca(value: Any) -> str:
    candidate = _meaningful_text(value)
    if len(candidate) < 8 or len(candidate) > 280:
        return ""
    normalized = candidate.casefold()
    if any(marker in normalized for marker in _RCA_NOISE_MARKERS):
        return ""
    alpha_numeric = sum(character.isalnum() for character in candidate)
    if alpha_numeric / max(1, len(candidate)) < 0.45:
        return ""
    return candidate


def _scenario_rca_candidate(
    scenario: dict[str, Any], ticket_evidence_by_id: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    values: dict[str, dict[str, Any]] = {}
    for ticket_id in scenario.get("ticket_ids", []):
        ticket_evidence = ticket_evidence_by_id.get(ticket_id, {})
        candidate = _usable_recorded_rca(
            ticket_evidence.get("RCA Root Cause")
            or ticket_evidence.get("rca_root_cause")
        )
        if not candidate:
            continue
        key = candidate.casefold()
        entry = values.setdefault(
            key,
            {"candidate": candidate, "ticket_ids": []},
        )
        entry["ticket_ids"].append(ticket_id)
    if not values:
        return None
    ordered = sorted(
        values.values(),
        key=lambda item: (-len(item["ticket_ids"]), item["candidate"].casefold()),
    )
    winner = ordered[0]
    ticket_count = _integer(scenario.get("ticket_count"))
    support_ticket_count = len(winner["ticket_ids"])
    support_ratio = support_ticket_count / ticket_count if ticket_count else 0.0
    runner_up_count = len(ordered[1]["ticket_ids"]) if len(ordered) > 1 else 0
    if len(ordered) > 1 and support_ticket_count <= runner_up_count:
        return None
    if ticket_count >= 5 and (
        support_ticket_count < 2 or support_ratio < 0.1
    ):
        return None
    return {
        "candidate": winner["candidate"],
        "ticket_ids": sorted(winner["ticket_ids"]),
        "support_ticket_count": support_ticket_count,
        "support_ratio": support_ratio,
    }


def _match_clean_text(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", _normalized_text(value))


def _character_bigrams(value: Any) -> set[str]:
    cleaned = _match_clean_text(value)
    if len(cleaned) < 2:
        return {cleaned} if cleaned else set()
    return {cleaned[index:index + 2] for index in range(len(cleaned) - 1)}


def _english_tokens(value: Any) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]{3,}", _normalized_text(value))
        if token not in {"the", "and", "with", "after", "when", "for", "from"}
    }


def _source_match_score(scenario: dict[str, Any], source_node: dict[str, Any]) -> float:
    scenario_text = " ".join(
        _text(scenario.get(field))
        for field in (
            "customer_symptom",
            "trigger_condition",
            "observed_behaviour",
            "expected_behaviour",
            "rep_title",
        )
    )
    source_text = " ".join(
        _text(source_node.get(field))
        for field in (
            "source_text",
            "symptom",
            "possible_cause",
            "check_step",
            "solution",
        )
    )
    scenario_bigrams = _character_bigrams(scenario_text)
    source_bigrams = _character_bigrams(source_text)
    if not scenario_bigrams or not source_bigrams:
        return 0.0
    character_score = (
        2 * len(scenario_bigrams & source_bigrams)
        / (len(scenario_bigrams) + len(source_bigrams))
    )
    scenario_tokens = _english_tokens(scenario_text)
    source_tokens = _english_tokens(source_text)
    token_score = (
        len(scenario_tokens & source_tokens) / len(scenario_tokens | source_tokens)
        if scenario_tokens and source_tokens
        else 0.0
    )
    return round(max(character_score, token_score), 4)


def _complete_source_leaf(source_node: dict[str, Any]) -> bool:
    return bool(
        source_node.get("is_terminal")
        and _meaningful_text(source_node.get("possible_cause"))
        and _meaningful_text(source_node.get("check_step"))
        and _meaningful_text(source_node.get("solution"))
    )


def _matched_source_branches(
    scenario: dict[str, Any], source_graphs: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    matches = []
    for source_graph in source_graphs:
        for source_node in source_graph.get("nodes", []):
            if not _complete_source_leaf(source_node):
                continue
            score = _source_match_score(scenario, source_node)
            if score < _SOURCE_MATCH_MEDIUM:
                continue
            matches.append((score, source_graph, source_node))
    matches.sort(
        key=lambda item: (-item[0], item[1].get("tree_id", ""), item[2].get("node_id", ""))
            )
    selected = []
    for score, source_graph, source_node in matches:
        if len(selected) >= _MAX_SOURCE_EXPERIENCE_BRANCHES:
            break
        source_node_id = _text(source_node.get("node_id"))
        if any(
            branch["source_reference"]["source_node_id"] == source_node_id
            for branch in selected
        ):
            continue
        selected.append({
            "branch_id": (
                f"EXP-{scenario['scenario_id']}-{source_graph['tree_id']}-"
                f"{source_node_id}"
            ),
            "basis": "after_sales_experience_source_path",
            "status": "source_experience_reference_candidate",
            "match_confidence": (
                "high" if score >= _SOURCE_MATCH_STRONG else "medium"
            ),
            "match_score": score,
            "possible_cause": _meaningful_text(source_node.get("possible_cause")),
            "check_steps": _meaningful_text(source_node.get("check_step")),
            "resolution": _meaningful_text(source_node.get("solution")),
            "resolution_status": "after_sales_source_experience_reference",
            "escalation": _meaningful_text(source_node.get("escalation_path")),
            "source_reference": {
                "reference_tree_id": _text(source_graph.get("reference_tree_id")),
                "source_tree_id": _text(source_graph.get("tree_id")),
                "source_node_id": source_node_id,
                "source_path": _text(source_node.get("source_path")),
                "source_asset": _text(source_graph.get("source_asset")),
                "source_match_score": score,
            },
            "ticket_evidence": [],
        })
    return selected


def _evidence_collection_steps(scenario: dict[str, Any]) -> str:
    problem_text = " ".join(
        _normalized_text(scenario.get(field))
        for field in ("problem_type", "customer_symptom", "trigger_condition")
    )
    steps = [
        "Reproduce the exact trigger and record the observed versus expected behaviour.",
        "Capture the event time, vehicle software version, assigned ECU, and relevant logs.",
    ]
    if any(term in problem_text for term in ("account", "login", "auth", "activation", "entitlement", "账号", "登录", "激活", "订阅")):
        steps.append("Verify account, entitlement, activation, market, and vehicle binding state.")
    elif any(term in problem_text for term in ("navigation", "route", "map", "location", "导航", "地图", "定位", "poi")):
        steps.append("Verify destination or POI input, location context, map/account state, connectivity, and service response.")
    elif any(term in problem_text for term in ("connect", "pair", "bluetooth", "wifi", "connection", "连接", "配对", "蓝牙", "无线")):
        steps.append("Verify device compatibility, pairing state, transport link state, and protocol or connectivity logs.")
    elif any(term in problem_text for term in ("audio", "sound", "music", "声音", "音频", "播放")):
        steps.append("Verify source selection, audio route, volume/focus state, and audio-service logs.")
    elif any(term in problem_text for term in ("display", "screen", "black", "显示", "屏", "黑屏")):
        steps.append("Verify display path, application state, system resource state, and graphics or display logs.")
    elif any(term in problem_text for term in ("update", "upgrade", "ota", "升级", "更新", "编程")):
        steps.append("Verify preconditions, package integrity, software compatibility, and download or install logs.")
    else:
        steps.append("Collect feature-specific logs and compare the failing environment with a known-good environment.")
    return " ".join(steps)


def _ticket_recovery(scenario: dict[str, Any]) -> tuple[str, str]:
    action = _meaningful_text(scenario.get("recovery_action"))
    if action:
        return action, "ticket_recorded_workaround_not_approved"
    return "", "no_recorded_recovery"


def _scenario_context_nodes(scenario: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "node_id": "ENTRY",
            "parent_node_id": "",
            "node_type": "Function Entry",
            "label": _text(scenario.get("service_function_label"))
            or _text(scenario.get("service_function")),
            "evidence_status": "service_function_mapping",
        },
        {
            "node_id": "SYMPTOM",
            "parent_node_id": "ENTRY",
            "node_type": "Customer Symptom",
            "label": _text(scenario.get("customer_symptom")),
            "evidence_status": "v12_customer_symptom",
        },
        {
            "node_id": "TRIGGER",
            "parent_node_id": "SYMPTOM",
            "node_type": "Trigger Condition",
            "label": _text(scenario.get("trigger_condition")),
            "evidence_status": "v12_trigger_condition",
        },
        {
            "node_id": "OBSERVED",
            "parent_node_id": "TRIGGER",
            "node_type": "Observed Behaviour",
            "label": _text(scenario.get("observed_behaviour"))
            or _text(scenario.get("customer_symptom")),
            "evidence_status": "v12_observed_behaviour",
        },
    ]


def build_scenario_diagnosis_path(
    *,
    scenario: dict[str, Any],
    source_graphs: list[dict[str, Any]],
    ticket_evidence_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build one continuous evidence-governed path for a concrete Scenario ID."""
    scenario_id = _text(scenario.get("scenario_id"))
    if not scenario_id:
        raise ValueError("scenario_id is required")
    branches = _matched_source_branches(scenario, source_graphs)
    recorded_rca = _scenario_rca_candidate(scenario, ticket_evidence_by_id)
    recovery_action, recovery_status = _ticket_recovery(scenario)
    if recorded_rca:
        branches.append({
            "branch_id": f"RCA-{scenario_id}",
            "basis": "ticket_recorded_rca_candidate",
            "status": "ticket_recorded_candidate_review_required",
            "match_confidence": "not_applicable",
            "match_score": 0.0,
            "possible_cause": recorded_rca["candidate"],
            "check_steps": (
                "Validate the ticket-recorded candidate against the exact trigger, "
                "ECU/software state, logs, and reproduction evidence. "
                + _evidence_collection_steps(scenario)
            ),
            "resolution": recovery_action,
            "resolution_status": recovery_status,
            "escalation": "Prepare an engineering evidence package if the candidate cannot be validated.",
            "source_reference": {},
            "ticket_evidence": recorded_rca,
        })
    if not branches:
        branches.append({
            "branch_id": f"EVIDENCE-{scenario_id}",
            "basis": "evidence_collection_template",
            "status": "no_verified_causal_evidence",
            "match_confidence": "not_applicable",
            "match_score": 0.0,
            "possible_cause": "No verified technical cause recorded",
            "check_steps": _evidence_collection_steps(scenario),
            "resolution": recovery_action,
            "resolution_status": recovery_status,
            "escalation": "Prepare an engineering evidence package",
            "source_reference": {},
            "ticket_evidence": {},
        })

    context_nodes = _scenario_context_nodes(scenario)
    path_edges = [
        {"from_node_id": "ENTRY", "to_node_id": "SYMPTOM", "relation": "reports"},
        {"from_node_id": "SYMPTOM", "to_node_id": "TRIGGER", "relation": "occurs_when"},
        {"from_node_id": "TRIGGER", "to_node_id": "OBSERVED", "relation": "produces"},
    ]
    for branch in branches:
        branch["nodes"] = [
            {
                "node_id": f"CAUSE-{branch['branch_id']}",
                "parent_node_id": "OBSERVED",
                "node_type": "Possible Cause",
                "label": branch["possible_cause"],
                "evidence_status": branch["status"],
            },
            {
                "node_id": f"CHECK-{branch['branch_id']}",
                "parent_node_id": f"CAUSE-{branch['branch_id']}",
                "node_type": "Check Steps",
                "label": branch["check_steps"],
                "evidence_status": branch["basis"],
            },
            {
                "node_id": f"OUTCOME-{branch['branch_id']}",
                "parent_node_id": f"CHECK-{branch['branch_id']}",
                "node_type": "Solution / Escalation",
                "label": branch["resolution"] or branch["escalation"],
                "evidence_status": branch["resolution_status"],
            },
        ]
        path_edges.extend([
            {
                "from_node_id": "OBSERVED",
                "to_node_id": branch["nodes"][0]["node_id"],
                "relation": "investigate_as",
            },
            {
                "from_node_id": branch["nodes"][0]["node_id"],
                "to_node_id": branch["nodes"][1]["node_id"],
                "relation": "check_with",
            },
            {
                "from_node_id": branch["nodes"][1]["node_id"],
                "to_node_id": branch["nodes"][2]["node_id"],
                "relation": "resolve_or_escalate",
            },
        ])
    return {
        "path_id": f"SDP-{scenario_id}",
        "service_function_id": _text(scenario.get("service_function_id")),
        "service_function": _text(scenario.get("service_function")),
        "service_function_label": _text(scenario.get("service_function_label")),
        "scenario_id": scenario_id,
        "problem_type": _text(scenario.get("problem_type")),
        "customer_symptom": _text(scenario.get("customer_symptom")),
        "trigger_condition": _text(scenario.get("trigger_condition")),
        "observed_behaviour": _text(scenario.get("observed_behaviour")),
        "expected_behaviour": _text(scenario.get("expected_behaviour")),
        "representative_title": _text(scenario.get("rep_title")),
        "representative_ticket_id": _text(scenario.get("rep_ticket_id")),
        "source_ticket_ids": list(scenario.get("ticket_ids") or []),
        "source_ticket_count": _integer(scenario.get("ticket_count")),
        "risk_band": _text(scenario.get("risk_band")),
        "risk_index": scenario.get("risk_index") or "",
        "evidence_confidence": _text(scenario.get("evidence_confidence")),
        "source_graph_count": len(source_graphs),
        "context_nodes": context_nodes,
        "diagnostic_branches": branches,
        "edges": path_edges,
    }


def build_scenario_diagnosis_catalog_from_files(
    *,
    scenario_workbook_path: Path,
    knowledge_base_path: Path,
    source_workbook_path: Path,
    registry_path: Path,
    manifest_path: Path,
    extracted_directory: Path,
) -> dict[str, Any]:
    """Build scenario-specific paths for all V12 service functions."""
    knowledge_base = _load_json_object(knowledge_base_path)
    scenarios, service_metadata = load_v12_scenario_paths(
        scenario_workbook_path, knowledge_base_path
    )
    source_graphs_by_service, unresolved_references = _load_source_graphs(
        knowledge_base=knowledge_base,
        registry_path=registry_path,
        manifest_path=manifest_path,
        extracted_directory=extracted_directory,
    )
    ticket_evidence_by_id = load_ticket_diagnostic_evidence(source_workbook_path)
    scenarios_by_service: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for scenario in scenarios:
        scenarios_by_service[scenario["service_function_id"]].append(scenario)

    functions = []
    for metadata in service_metadata:
        service_function_id = metadata["service_function_id"]
        source_graphs = source_graphs_by_service.get(service_function_id, [])
        scenario_paths = [
            build_scenario_diagnosis_path(
                scenario=scenario,
                source_graphs=source_graphs,
                ticket_evidence_by_id=ticket_evidence_by_id,
            )
            for scenario in scenarios_by_service.get(service_function_id, [])
        ]
        scenario_paths.sort(
            key=lambda path: (-path["source_ticket_count"], path["scenario_id"])
        )
        functions.append({
            **metadata,
            "scenario_count": len(scenario_paths),
            "ticket_count": sum(
                path["source_ticket_count"] for path in scenario_paths
            ),
            "source_graphs": source_graphs,
            "scenario_paths": scenario_paths,
        })

    functions.sort(
        key=lambda function: (
            function["service_domain"].casefold(),
            function["service_function_label"].casefold(),
        )
    )
    paths = [path for function in functions for path in function["scenario_paths"]]
    branches = [
        branch
        for path in paths
        for branch in path["diagnostic_branches"]
    ]
    return {
        "schema_version": "phase09-scenario-diagnosis-tree-catalog-v2",
        "data_policy": {
            "scenario_policy": (
                "Each Scenario ID keeps its original V12 symptom, trigger, observed "
                "behaviour, expected behaviour, and ticket lineage."
            ),
            "source_experience_policy": (
                "After-sales source-tree paths are shown only as AI-matched experience "
                "reference candidates. The original source image remains available."
            ),
            "ticket_rca_policy": (
                "Ticket RCA text is a review-required recorded candidate, never a "
                "confirmed cause. Ticket recovery text is not an approved repair."
            ),
            "evidence_gap_policy": (
                "When neither an experience path nor usable ticket RCA is available, "
                "the path shows evidence collection and engineering escalation rather "
                "than an invented cause or repair."
            ),
        },
        "summary": {
            "service_function_count": len(functions),
            "scenario_path_count": len(paths),
            "field_ticket_count": sum(
                path["source_ticket_count"] for path in paths
            ),
            "image_backed_function_count": sum(
                bool(function["source_graphs"]) for function in functions
            ),
            "ai_structured_source_graph_count": sum(
                len(function["source_graphs"]) for function in functions
            ),
            "source_experience_candidate_path_count": sum(
                branch["basis"] == "after_sales_experience_source_path"
                for branch in branches
            ),
            "ticket_rca_candidate_path_count": sum(
                branch["basis"] == "ticket_recorded_rca_candidate"
                for branch in branches
            ),
            "evidence_collection_path_count": sum(
                branch["basis"] == "evidence_collection_template"
                for branch in branches
            ),
            "unresolved_reference_count": len(unresolved_references),
        },
        "unresolved_references": unresolved_references,
        "functions": functions,
    }


def _serialized_html_data(value: Any) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def render_scenario_diagnosis_catalog_html(catalog: dict[str, Any]) -> str:
    """Render the scenario-level catalog as a standalone local HTML page."""
    serialized_catalog = _serialized_html_data(catalog)
    template = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>All-Function Scenario Diagnosis Tree Catalog</title>
<style>
:root { color:#17242c; background:#edf1ef; font-family:Aptos,"Noto Sans SC","Microsoft YaHei",sans-serif; }
* { box-sizing:border-box; }
html,body { overflow-x:hidden; }
body { margin:0; }
main { max-width:1720px; margin:0 auto; padding:26px 28px 48px; }
header { border-bottom:4px solid #187166; padding-bottom:17px; }
h1 { margin:0; color:#123c4a; font-size:28px; letter-spacing:0; }
p { line-height:1.5; }
.subtitle { margin:7px 0 0; color:#52616b; font-size:15px; }
.summary { display:grid; grid-template-columns:repeat(7,minmax(112px,1fr)); gap:1px; margin-top:20px; border:1px solid #c7d0d6; background:#c7d0d6; }
.summary div { min-width:0; min-height:66px; padding:10px 12px; background:#fff; }
.summary span { display:block; color:#61717a; font-size:12px; }
.summary strong { display:block; margin-top:5px; color:#173c49; font-size:17px; overflow-wrap:anywhere; }
.controls { display:grid; grid-template-columns:minmax(210px,.55fr) minmax(260px,.85fr) minmax(360px,1.6fr); gap:14px; margin:20px 0 14px; }
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
.diagram-scroll { min-width:0; overflow:auto; min-height:470px; max-height:780px; }
svg { display:block; min-width:1380px; width:100%; height:auto; }
.source-image { display:block; width:100%; min-width:940px; height:auto; }
.empty-state { padding:28px; color:#536670; line-height:1.5; }
.detail-panel { position:sticky; top:12px; }
.detail-panel dl { margin:0; }
.detail-panel div { padding:10px 12px; border-bottom:1px solid #d4dde0; }
.detail-panel div:last-child { border-bottom:0; }
.detail-panel dt { color:#61717a; font-size:12px; }
.detail-panel dd { margin:4px 0 0; color:#1b3038; font-size:13px; line-height:1.45; white-space:pre-wrap; overflow-wrap:anywhere; }
.section-title { margin:25px 0 9px; color:#123c4a; font-size:18px; }
.table-shell { overflow:auto; max-height:540px; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th,td { min-width:0; padding:8px 9px; border:1px solid #c7d0d6; text-align:left; vertical-align:top; line-height:1.4; overflow-wrap:anywhere; }
th { position:sticky; top:0; z-index:1; background:#123c4a; color:#fff; white-space:nowrap; }
td.count { text-align:right; white-space:nowrap; }
.badge { display:inline-block; padding:2px 6px; border:1px solid #adbdc2; border-radius:3px; background:#f5f8f8; color:#35515b; font-size:12px; }
@media (max-width:980px) { main { padding:20px 16px 34px; } h1 { font-size:23px; } .summary,.tree-meta { grid-template-columns:repeat(2,minmax(0,1fr)); } .controls,.workspace { grid-template-columns:1fr; } .detail-panel { position:static; } .diagram-scroll { min-height:380px; } }
</style>
</head>
<body>
<main>
  <header>
    <h1>All-Function Scenario Diagnosis Tree Catalog</h1>
    <p class="subtitle">Customer Symptom -> Trigger -> Possible Cause -> Check Steps -> Solution / Escalation. Each displayed path is one concrete V12 failure scenario, not a symptom/trigger aggregate.</p>
  </header>
  <section class="summary" aria-label="Catalog summary">
    <div><span>Service functions</span><strong id="summary-functions"></strong></div>
    <div><span>Failure scenarios</span><strong id="summary-scenarios"></strong></div>
    <div><span>Field tickets</span><strong id="summary-tickets"></strong></div>
    <div><span>Image-backed functions</span><strong id="summary-image-functions"></strong></div>
    <div><span>Experience candidates</span><strong id="summary-experience"></strong></div>
    <div><span>Ticket RCA candidates</span><strong id="summary-rca"></strong></div>
    <div><span>Evidence-only paths</span><strong id="summary-gaps"></strong></div>
  </section>
  <section class="controls">
    <label for="function-filter">Find a function<input id="function-filter" type="search" placeholder="Function, domain, or ID"></label>
    <label for="function-selector">Service function<select id="function-selector"></select></label>
    <label for="scenario-selector">Concrete failure scenario<select id="scenario-selector"></select></label>
  </section>
  <section class="tree-meta">
    <div><span>Function</span><strong id="meta-function"></strong></div>
    <div><span>Scenario ID</span><strong id="meta-scenario"></strong></div>
    <div><span>Problem type</span><strong id="meta-problem"></strong></div>
    <div><span>Scenario tickets</span><strong id="meta-tickets"></strong></div>
    <div><span>Risk band</span><strong id="meta-risk"></strong></div>
    <div><span>Experience source</span><strong id="meta-source"></strong></div>
  </section>
  <p class="governance" id="governance-note"></p>
  <nav class="tabs" role="tablist" aria-label="Tree views">
    <button class="tab" type="button" role="tab" data-view="scenario" aria-selected="true">Scenario Diagnosis Path</button>
    <button class="tab" type="button" role="tab" data-view="source" aria-selected="false">After-Sales Experience Tree</button>
    <button class="tab" type="button" role="tab" data-view="image" aria-selected="false">Original Source Image</button>
  </nav>
  <section class="workspace">
    <section class="diagram-panel">
      <header class="panel-head"><span id="diagram-title"></span><select id="source-selector" aria-label="After-sales experience source"></select></header>
      <div class="diagram-scroll" id="diagram-scroll"></div>
    </section>
    <aside class="detail-panel"><header class="panel-head">Selected path evidence</header><dl id="detail-panel"></dl></aside>
  </section>
  <h2 class="section-title">Scenario route stages</h2>
  <section class="table-shell"><table><thead><tr><th>Stage</th><th>Evidence / Instruction</th><th>Basis</th><th>Status</th></tr></thead><tbody id="stage-rows"></tbody></table></section>
  <h2 class="section-title">Failure scenarios in this function</h2>
  <section class="table-shell"><table><thead><tr><th>Scenario</th><th>Customer symptom</th><th>Trigger</th><th>Observed behaviour</th><th>Expected behaviour</th><th>Tickets</th><th>Evidence confidence</th></tr></thead><tbody id="scenario-rows"></tbody></table></section>
</main>
<script id="catalog-data" type="application/json">__CATALOG_DATA__</script>
<script>
const catalog = JSON.parse(document.getElementById("catalog-data").textContent);
const NS = "http://www.w3.org/2000/svg";
const scenarioColors = { "Function Entry":"#d7ead1", "Customer Symptom":"#d8e8f5", "Trigger Condition":"#fff0c8", "Observed Behaviour":"#e8ddf3", "Possible Cause":"#f3dfc5", "Check Steps":"#dbece7", "Solution / Escalation":"#e4e9ec" };
const sourceColors = { start:"#d7ead1", decision:"#d8e8f5", resolution:"#e4e9ec", action:"#fff0c8", escalation:"#f7d7d2", note:"#eceff1" };
let activeFunction = null;
let activeScenario = null;
let activeView = "scenario";
let activeSourceIndex = 0;
let activeStage = null;
function text(value) { return String(value || ""); }
function svgElement(name, attributes = {}) { const element = document.createElementNS(NS, name); Object.entries(attributes).forEach(([key,value]) => element.setAttribute(key,text(value))); return element; }
function activeSourceGraph() { return activeFunction && activeFunction.source_graphs[activeSourceIndex] || null; }
function sourceGraphNodes() { const graph=activeSourceGraph(); return graph ? graph.nodes : []; }
function wrapLines(value, limit) { const characters=Array.from(text(value)); const lines=[]; while(characters.length && lines.length<4) lines.push(characters.splice(0,limit).join("")); return lines.length ? lines : [""]; }
function drawText(svg,value,x,y,limit,color="#17242c",fontSize=12) { const lines=wrapLines(value,limit); const label=svgElement("text",{x,y:y-(lines.length-1)*8,"text-anchor":"middle","font-size":fontSize,fill:color}); lines.forEach((line,index)=>{ const span=svgElement("tspan",{x,dy:index?15:0}); span.textContent=line; label.appendChild(span); }); svg.appendChild(label); }
function arrow(svg,from,to,markerId) { svg.appendChild(svgElement("line",{x1:from.x+from.right,y1:from.y,x2:to.x-to.left,y2:to.y,stroke:"#536670","stroke-width":1.35,"marker-end":`url(#${markerId})`})); }
function diagramNode(svg,node,position,fill,onSelect) { const group=svgElement("g",{tabindex:0,role:"button",cursor:"pointer","aria-label":`${node.node_type}: ${node.label}`}); const title=svgElement("title"); title.textContent=`${node.node_type}: ${node.label}`; group.appendChild(title); group.appendChild(svgElement("rect",{x:position.x-position.left,y:position.y-48,width:position.left*2,height:96,rx:5,fill,stroke:activeStage && activeStage.node_id===node.node_id?"#b55a19":"#59717c","stroke-width":activeStage && activeStage.node_id===node.node_id?3:1.2})); svg.appendChild(group); drawText(svg,node.node_type,position.x,position.y-28,24,"#52616b",10); drawText(svg,node.label,position.x,position.y+4,27); drawText(svg,node.evidence_status||"",position.x,position.y+34,26,"#52616b",10); group.addEventListener("click",()=>{ activeStage=node; renderAll(); }); group.addEventListener("keydown",event=>{ if(event.key==="Enter"||event.key===" "){event.preventDefault();activeStage=node;renderAll();} }); }
function renderScenarioDiagram() { const container=document.getElementById("diagram-scroll"); container.replaceChildren(); document.getElementById("diagram-title").textContent="Scenario Diagnosis Path"; if(!activeScenario){ const empty=document.createElement("p"); empty.className="empty-state"; empty.textContent="No V12 field scenario is mapped to this service function."; container.appendChild(empty); return; } const context=activeScenario.context_nodes; const branches=activeScenario.diagnostic_branches; const height=Math.max(500,180+branches.length*170); const svg=svgElement("svg",{viewBox:`0 0 1650 ${height}`,role:"img","aria-label":"Scenario diagnosis path"}); const defs=svgElement("defs"); const marker=svgElement("marker",{id:"scenario-arrow",markerWidth:8,markerHeight:8,refX:7,refY:4,orient:"auto",markerUnits:"strokeWidth"}); marker.appendChild(svgElement("path",{d:"M 0 0 L 8 4 L 0 8 z",fill:"#536670"})); defs.appendChild(marker); svg.appendChild(defs); const y=height/2; const positions={ ENTRY:{x:110,y,left:92,right:92}, SYMPTOM:{x:350,y,left:122,right:122}, TRIGGER:{x:630,y,left:122,right:122}, OBSERVED:{x:900,y,left:122,right:122} }; const byId=new Map(context.map(node=>[node.node_id,node])); arrow(svg,positions.ENTRY,positions.SYMPTOM,"scenario-arrow"); arrow(svg,positions.SYMPTOM,positions.TRIGGER,"scenario-arrow"); arrow(svg,positions.TRIGGER,positions.OBSERVED,"scenario-arrow"); context.forEach(node=>diagramNode(svg,node,positions[node.node_id],scenarioColors[node.node_type]||"#eceff1")); branches.forEach((branch,index)=>{ const branchY=120+index*170; const cause=branch.nodes[0]; const check=branch.nodes[1]; const outcome=branch.nodes[2]; const causePosition={x:1160,y:branchY,left:132,right:132}; const checkPosition={x:1400,y:branchY,left:124,right:124}; const outcomePosition={x:1600,y:branchY,left:108,right:108}; arrow(svg,positions.OBSERVED,causePosition,"scenario-arrow"); arrow(svg,causePosition,checkPosition,"scenario-arrow"); arrow(svg,checkPosition,outcomePosition,"scenario-arrow"); diagramNode(svg,cause,causePosition,scenarioColors[cause.node_type]); diagramNode(svg,check,checkPosition,scenarioColors[check.node_type]); diagramNode(svg,outcome,outcomePosition,scenarioColors[outcome.node_type]); }); container.appendChild(svg); }
function renderSourceDiagram() { const container=document.getElementById("diagram-scroll"); container.replaceChildren(); document.getElementById("diagram-title").textContent="After-Sales Experience Tree"; const graph=activeSourceGraph(); if(!graph){ const empty=document.createElement("p"); empty.className="empty-state"; empty.textContent="No original after-sales source tree is mapped to this service function."; container.appendChild(empty); return; } const nodes=sourceGraphNodes(); const byId=new Map(nodes.map(node=>[node.node_id,node])); const children=new Map(); nodes.forEach(node=>children.set(node.node_id,[])); graph.edges.forEach(edge=>{if(children.has(edge.from_node_id)&&byId.has(edge.to_node_id))children.get(edge.from_node_id).push(edge.to_node_id);}); const root=nodes.find(node=>node.node_id==="ROOT")||nodes[0]; const depth=new Map([[root.node_id,0]]); const queue=[root.node_id]; for(let index=0;index<queue.length;index+=1){const current=queue[index];(children.get(current)||[]).forEach(child=>{if(!depth.has(child)){depth.set(child,(depth.get(current)||0)+1);queue.push(child);}});} const levels=new Map(); nodes.forEach(node=>{const level=depth.get(node.node_id)||0;if(!levels.has(level))levels.set(level,[]);levels.get(level).push(node);}); const maxDepth=Math.max(...levels.keys()); const maxRows=Math.max(...Array.from(levels.values(),items=>items.length)); const width=Math.max(960,160+(maxDepth+1)*270); const height=Math.max(380,110+maxRows*108); const svg=svgElement("svg",{viewBox:`0 0 ${width} ${height}`,role:"img","aria-label":"After-sales experience tree"}); const defs=svgElement("defs"); const marker=svgElement("marker",{id:"source-arrow",markerWidth:8,markerHeight:8,refX:7,refY:4,orient:"auto",markerUnits:"strokeWidth"}); marker.appendChild(svgElement("path",{d:"M 0 0 L 8 4 L 0 8 z",fill:"#536670"})); defs.appendChild(marker); svg.appendChild(defs); const positions=new Map(); Array.from(levels.entries()).forEach(([level,items])=>{items.sort((left,right)=>left.node_id.localeCompare(right.node_id));const gap=height/(items.length+1);items.forEach((node,index)=>positions.set(node.node_id,{x:140+level*270,y:gap*(index+1),left:92,right:92}));}); graph.edges.forEach(edge=>{const from=positions.get(edge.from_node_id),to=positions.get(edge.to_node_id);if(from&&to)arrow(svg,from,to,"source-arrow");}); nodes.forEach(raw=>{const node={node_id:raw.node_id,node_type:raw.node_type,label:raw.source_text,evidence_status:raw.review_status,raw};diagramNode(svg,node,positions.get(raw.node_id),sourceColors[raw.node_type]||"#eceff1");}); container.appendChild(svg); }
function renderImage() { const container=document.getElementById("diagram-scroll"); container.replaceChildren(); document.getElementById("diagram-title").textContent="Original Source Image"; const graph=activeSourceGraph(); if(!graph){ const empty=document.createElement("p"); empty.className="empty-state"; empty.textContent="No original after-sales source image is mapped to this service function."; container.appendChild(empty); return; } const image=document.createElement("img"); image.className="source-image"; image.src=graph.source_asset||""; image.alt=`${graph.tree_name_en||graph.tree_id} original after-sales source image`; container.appendChild(image); }
function detailItem(label,value) { const item=document.createElement("div"); const term=document.createElement("dt"); const definition=document.createElement("dd"); term.textContent=label; definition.textContent=text(value)||"Not recorded"; item.append(term,definition); return item; }
function renderDetail() { const root=document.getElementById("detail-panel"); root.replaceChildren(); if(activeView==="scenario"&&activeScenario){ const branch=activeScenario.diagnostic_branches[0]; root.append(detailItem("Customer symptom",activeScenario.customer_symptom),detailItem("Trigger condition",activeScenario.trigger_condition),detailItem("Observed behaviour",activeScenario.observed_behaviour),detailItem("Expected behaviour",activeScenario.expected_behaviour),detailItem("Representative ticket",activeScenario.representative_ticket_id),detailItem("Selected cause",activeStage?.label||branch?.possible_cause),detailItem("Evidence basis",activeStage?.evidence_status||branch?.status)); return; } const graph=activeSourceGraph(); const raw=activeStage?.raw||graph?.nodes?.[0]; if(!raw){root.append(detailItem("Evidence","No source tree selected."));return;} root.append(detailItem("Source path",raw.source_path),detailItem("Symptom",raw.symptom),detailItem("Possible cause",raw.possible_cause),detailItem("Check step",raw.check_step),detailItem("Source solution",raw.solution),detailItem("Escalation",raw.escalation_path),detailItem("Review status",raw.review_status)); }
function renderStageRows() { const body=document.getElementById("stage-rows"); body.replaceChildren(); if(!activeScenario)return; const rows=[...activeScenario.context_nodes,...activeScenario.diagnostic_branches.flatMap(branch=>branch.nodes.map(node=>({...node,basis:branch.basis,status:branch.status})) )]; rows.forEach(row=>{const tableRow=document.createElement("tr"); [[row.node_type,""],[row.label,""],[row.basis||row.evidence_status||""],[row.status||row.evidence_status||""]].forEach(([value,className])=>{const cell=document.createElement("td");cell.textContent=text(value);if(className)cell.className=className;tableRow.appendChild(cell);});tableRow.addEventListener("click",()=>{activeStage=row;renderAll();});body.appendChild(tableRow);}); }
function renderScenarioRows() { const body=document.getElementById("scenario-rows"); body.replaceChildren(); (activeFunction?.scenario_paths||[]).forEach(path=>{const row=document.createElement("tr"); [path.scenario_id,path.customer_symptom,path.trigger_condition,path.observed_behaviour,path.expected_behaviour,path.source_ticket_count,path.evidence_confidence].forEach((value,index)=>{const cell=document.createElement("td");cell.textContent=text(value);if(index===5)cell.className="count";row.appendChild(cell);});row.addEventListener("click",()=>{document.getElementById("scenario-selector").value=path.scenario_id;activeScenario=path;activeStage=null;activeView="scenario";renderAll();});body.appendChild(row);}); }
function populateSourceSelector() { const selector=document.getElementById("source-selector");selector.replaceChildren();(activeFunction?.source_graphs||[]).forEach((graph,index)=>{const option=document.createElement("option");option.value=String(index);option.textContent=`${graph.reference_tree_id||graph.tree_id} | ${graph.tree_name_en||graph.tree_id}`;selector.appendChild(option);});selector.disabled=activeView==="scenario"||!(activeFunction?.source_graphs||[]).length;if(selector.options.length)selector.value=String(activeSourceIndex); }
function renderMeta() { const path=activeScenario; document.getElementById("meta-function").textContent=activeFunction?.service_function_label||"";document.getElementById("meta-scenario").textContent=path?.scenario_id||"No field scenario";document.getElementById("meta-problem").textContent=path?.problem_type||"";document.getElementById("meta-tickets").textContent=path?String(path.source_ticket_count):"0";document.getElementById("meta-risk").textContent=path?.risk_band||"";document.getElementById("meta-source").textContent=(activeFunction?.source_graphs||[]).length?`${activeFunction.source_graphs.length} source tree(s)`:"No source tree";document.getElementById("governance-note").textContent=path?"Experience branches are AI matches to the original after-sales source tree. Ticket RCA is review-required evidence, ticket recovery can be a workaround, and evidence-only branches intentionally do not invent a cause or repair.":"This service function has no V12 field scenario. The after-sales source tree may still be available in the other tabs.";}
function renderDiagram() { if(activeView==="scenario")renderScenarioDiagram();else if(activeView==="source")renderSourceDiagram();else renderImage(); }
function renderAll() { renderMeta();populateSourceSelector();renderDiagram();renderDetail();renderStageRows();renderScenarioRows();document.querySelectorAll(".tab").forEach(tab=>tab.setAttribute("aria-selected",String(tab.dataset.view===activeView))); }
function populateScenarioSelector() { const selector=document.getElementById("scenario-selector");selector.replaceChildren();const paths=activeFunction?.scenario_paths||[];if(!paths.length){const option=document.createElement("option");option.value="";option.textContent="No V12 field scenario";selector.appendChild(option);selector.disabled=true;activeScenario=null;return;}selector.disabled=false;paths.forEach(path=>{const option=document.createElement("option");option.value=path.scenario_id;option.textContent=`${path.scenario_id} | ${text(path.customer_symptom).slice(0,90)} (${path.source_ticket_count} tickets)`;selector.appendChild(option);});activeScenario=paths.find(path=>path.scenario_id===selector.value)||paths[0];selector.value=activeScenario.scenario_id; }
function populateFunctionSelector(filterText="") { const selector=document.getElementById("function-selector");const selectedId=activeFunction?.service_function_id||"";const filter=filterText.trim().toLocaleLowerCase();selector.replaceChildren();catalog.functions.filter(item=>!filter||[item.service_function_id,item.service_function_label,item.service_domain].join(" ").toLocaleLowerCase().includes(filter)).forEach(item=>{const option=document.createElement("option");option.value=item.service_function_id;option.textContent=`${item.service_function_label} | ${item.scenario_count} scenarios / ${item.ticket_count} tickets`;selector.appendChild(option);});if(!selector.options.length){const option=document.createElement("option");option.value="";option.textContent="No matching service function";selector.appendChild(option);return;}selector.value=Array.from(selector.options).some(option=>option.value===selectedId)?selectedId:selector.options[0].value; }
function selectFunction(serviceFunctionId) { activeFunction=catalog.functions.find(item=>item.service_function_id===serviceFunctionId)||catalog.functions[0]||null;activeSourceIndex=0;activeStage=null;populateScenarioSelector();renderAll(); }
function initializeSummary() { const summary=catalog.summary||{};document.getElementById("summary-functions").textContent=text(summary.service_function_count);document.getElementById("summary-scenarios").textContent=text(summary.scenario_path_count);document.getElementById("summary-tickets").textContent=text(summary.field_ticket_count);document.getElementById("summary-image-functions").textContent=text(summary.image_backed_function_count);document.getElementById("summary-experience").textContent=text(summary.source_experience_candidate_path_count);document.getElementById("summary-rca").textContent=text(summary.ticket_rca_candidate_path_count);document.getElementById("summary-gaps").textContent=text(summary.evidence_collection_path_count); }
document.getElementById("function-filter").addEventListener("input",event=>{populateFunctionSelector(event.target.value);selectFunction(document.getElementById("function-selector").value);});
document.getElementById("function-selector").addEventListener("change",event=>selectFunction(event.target.value));
document.getElementById("scenario-selector").addEventListener("change",event=>{activeScenario=(activeFunction?.scenario_paths||[]).find(path=>path.scenario_id===event.target.value)||null;activeStage=null;activeView="scenario";renderAll();});
document.getElementById("source-selector").addEventListener("change",event=>{activeSourceIndex=Number(event.target.value||0);activeStage=null;renderAll();});
document.querySelectorAll(".tab").forEach(tab=>tab.addEventListener("click",()=>{activeView=tab.dataset.view;activeStage=null;renderAll();}));
initializeSummary();populateFunctionSelector();selectFunction(document.getElementById("function-selector").value);
</script>
</body>
</html>
"""
    return template.replace("__CATALOG_DATA__", serialized_catalog)


def write_scenario_diagnosis_catalog_artifacts(
    *, catalog: dict[str, Any], output_html: Path, output_json: Path
) -> None:
    """Write standalone V2 HTML and JSON artifacts."""
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    output_html.write_text(
        render_scenario_diagnosis_catalog_html(catalog), encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build all-function scenario-level after-sales diagnosis trees."
    )
    parser.add_argument(
        "--scenario-workbook", type=Path, default=DEFAULT_SCENARIO_WORKBOOK
    )
    parser.add_argument("--knowledge-base", type=Path, default=DEFAULT_KNOWLEDGE_BASE)
    parser.add_argument("--source-workbook", type=Path, default=DEFAULT_SOURCE_WORKBOOK)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--extracted-directory", type=Path, default=DEFAULT_EXTRACTED_DIRECTORY
    )
    parser.add_argument("--output-html", type=Path, default=DEFAULT_OUTPUT_HTML)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    args = parser.parse_args(argv)
    catalog = build_scenario_diagnosis_catalog_from_files(
        scenario_workbook_path=args.scenario_workbook,
        knowledge_base_path=args.knowledge_base,
        source_workbook_path=args.source_workbook,
        registry_path=args.registry,
        manifest_path=args.manifest,
        extracted_directory=args.extracted_directory,
    )
    write_scenario_diagnosis_catalog_artifacts(
        catalog=catalog,
        output_html=args.output_html,
        output_json=args.output_json,
    )
    print(f"Saved HTML: {args.output_html}")
    print(f"Saved JSON: {args.output_json}")
    print(f"Service functions: {catalog['summary']['service_function_count']}")
    print(f"Scenario paths: {catalog['summary']['scenario_path_count']}")
    print(
        "Experience-reference candidates: "
        f"{catalog['summary']['source_experience_candidate_path_count']}"
    )
    print(
        "Ticket RCA candidates: "
        f"{catalog['summary']['ticket_rca_candidate_path_count']}"
    )
    print(
        "Evidence-only paths: "
        f"{catalog['summary']['evidence_collection_path_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())