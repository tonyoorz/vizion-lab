import json
from datetime import datetime

from openpyxl import load_workbook

from backend.analytics.open_shift_scenario_triple import (
    assess_post_target_recurrence,
    build_detailed_future_resolution_scenarios,
    build_detailed_future_resolution_trees,
    build_merged_shift_tolerate_artifact_triple,
    load_shift_full_records_from_source_workbook,
)
from backend.analytics.phase09_process_v12 import merge_shift_tolerate_records


def _record(
    ticket_id: str,
    *,
    phase: str,
    lifecycle_status: str = "",
    selection_scope: str = "",
    issue_summary: str = "Navigation guidance disappears after restart.",
    title: str = "Navigation guidance disappears after restart",
    future_resolution_plan: str = "Target I-Step: NA05-27-11-460",
) -> dict[str, object]:
    return {
        "ticket_id": ticket_id,
        "creation_time": "2026-09-18T08:30:45Z",
        "title": title,
        "phase": phase,
        "ticket_lifecycle_status": lifecycle_status,
        "selection_scope": selection_scope,
        "service_function_id": "NAVIGATION",
        "service_function": "Navigation",
        "service_mapping_source": "fixture",
        "service_domain": "Navigation / Location",
        "confidence_category": "Formal Shift/Tolerate process",
        "confidence_basis": "Recorded decision date and target release.",
        "issue_summary": issue_summary,
        "postponement_assessment": "Ticket has a future resolution plan.",
        "postponement_reason_analysis": "Release timing is recorded.",
        "resolution_timing_analysis": "Target week is CW47.",
        "resolution_version_analysis": "Target I-Step is NA05-27-11-460.",
        "review_focus": "Verify delivery evidence.",
        "future_resolution_status": "Recorded Shift/Tolerate process plan - not verified repair",
        "future_resolution_plan": future_resolution_plan,
        "source_software_version": "idcevo-pu2707-27w01.1-1",
        "source_release": "27-07",
        "target_i_step": "NA05-27-11-460",
        "target_release": "27-11",
        "shift_to_set_pu": "27-11",
        "first_use_sop_pu": "",
        "closed_in_version": "",
        "sw_hrl_closed_version": "",
        "app_version": "",
        "function_responsible": "Owner A",
        "solution_cluster": "Navigation CN",
        "product_areas": "Provide Navigation 2.0",
        "defect_category": "Navigation",
        "assigned_ecu": "IDCEVO-25",
        "llm_source": "fixture",
        "llm_model": "fixture-model",
        "description": "Guidance disappears after restart.",
        "comments": "Target is 27-11.",
        "plan_evidence_signals": future_resolution_plan,
        "process_tier": "Formal Shift/Tolerate process",
        "process_basis": "Recorded decision date.",
    }


def test_current_evidence_input_orders_comments_and_invalidates_changed_source() -> None:
    from backend.analytics.shift_current_evidence import build_current_evidence_input

    ticket = {
        "defect_id": "D-1", "name": "Blank navigation", "phase": "08-In Verification",
        "description": "2026-10-10: retest requested.",
        "comments": json.dumps([
            {"id": "new", "creation_time": "2026-10-10", "text": "Please retest before closure."},
            {"id": "old", "creation_time": "2026-05-05", "text": "Previous fixed statement was for another ticket; retracted."},
        ]),
    }
    record = build_current_evidence_input(ticket, [], source_fingerprint="first")
    evidence = record["ticket"]["evidence"]
    comments = [item for item in evidence if item["source"] == "comment"]
    assert [item["id"] for item in comments] == ["C-old", "C-new"]
    assert "retracted" in comments[0]["text"]
    assert record["input_version"] == "shift-current-evidence-v2"
    changed = build_current_evidence_input(ticket, [], source_fingerprint="changed")
    assert changed["input_fingerprint"] != record["input_fingerprint"]


def test_current_evidence_application_rejects_stale_summary_and_preserves_unavailable_scope() -> None:
    import pytest
    from backend.analytics.shift_current_evidence import apply_current_evidence

    baseline = [_record("D-1", phase="05-In Testing"), _record("D-2", phase="09-Concluded without action")]
    baseline[1].update({"evidence_fingerprint": "stale", "evidence_refreshed_at": "2026-01-01", "llm_input_fingerprint": "stale", "current_evidence_references": [{"id": "old"}]})
    inputs = [{"ticket_id": "D-1", "input_fingerprint": "fresh", "input_version": "shift-current-evidence-v2", "ticket": {"evidence": []}}]
    with pytest.raises(ValueError, match="fingerprint"):
        apply_current_evidence(baseline, {"D-1": {"input_fingerprint": "stale"}}, inputs, {"D-1": {"phase": "08-In Verification"}}, {"D-2"})
    historical = apply_current_evidence([baseline[1]], {}, [], {}, {"D-2"})[0]
    assert historical["source_evidence_status"] == "unavailable_preserved"
    assert historical["repair_status"] == "unknown"
    assert historical["ticket_lifecycle_status"] == "Historical / online unavailable"
    assert historical["llm_source"] != "llm"
    assert historical["historical_future_resolution_plan"] == baseline[1]["future_resolution_plan"]
    assert historical["historical_postponement_assessment"] == baseline[1]["postponement_assessment"]
    assert "online unavailable" in historical["future_resolution_plan"]
    assert historical["current_evidence_references"] == []
    assert historical["llm_input_fingerprint"] == ""
    assert historical["evidence_fingerprint"] == ""
    assert historical["evidence_refreshed_at"] == ""
    from backend.analytics.open_shift_scenario_triple import _resolution_plan_summary, _resolution_version_summary
    assert "unavailable" in _resolution_plan_summary(historical)
    assert "unavailable" in _resolution_version_summary(historical)


def test_current_evidence_downgrades_conflicting_lifecycle_and_delivery_claims() -> None:
    from backend.analytics.shift_current_evidence import ANALYSIS_FIELDS, CURRENT_STATE_INPUT_VERSION, apply_current_evidence

    summary = {
        **{field: "Recorded evidence requires final verification." for field in ANALYSIS_FIELDS},
        "input_fingerprint": "fresh", "input_version": CURRENT_STATE_INPUT_VERSION, "source": "llm",
        "historical_shift_status": "documented", "current_postponement_status": "not_pending",
        "repair_status": "verification_pending", "delivery_status": "planned",
        "status_evidence": {field: ["C-1"] for field in ("historical_shift_status", "current_postponement_status", "repair_status", "delivery_status")},
    }
    inputs = [{"ticket_id": "D-1", "input_fingerprint": "fresh", "ticket": {"evidence": [{"id": "C-1", "date": "2026-10-10", "text": "Fix reported; future delivery and retest pending."}]}}]
    record = apply_current_evidence([_record("D-1", phase="05-In Testing")], {"D-1": summary}, inputs, {"D-1": {"phase": "08-In Verification"}}, set())[0]
    assert record["current_postponement_status"] == "unclear"
    assert record["llm_current_postponement_status"] == "not_pending"
    assert record["assessment_warnings"]
    summary["current_postponement_status"] = "pending"
    closed = apply_current_evidence([_record("D-1", phase="05-In Testing")], {"D-1": summary}, inputs, {"D-1": {"phase": "06-Concluded"}}, set())[0]
    assert closed["current_postponement_status"] == "unclear"
    assert closed["ticket_lifecycle_status"] == "Closed / Phase 06"


def test_current_source_gate_rejects_missing_provenance_and_inconsistent_comments(tmp_path) -> None:
    import sqlite3
    from types import SimpleNamespace
    import pytest
    from backend.analytics.ingest.pipeline import refresh_octane_tickets_by_ids
    from backend.analytics.shift_current_evidence import load_current_evidence_inputs

    database_path = tmp_path / "source.db"
    connection = sqlite3.connect(database_path)
    connection.close()
    with pytest.raises(ValueError, match="provenance"):
        load_current_evidence_inputs(database_path, ["D-1"], set())
    client = SimpleNamespace(
        fetch_rows=lambda endpoint, **kwargs: [{"id": "D-1", "name": "Fixture"}],
        fetch_comments_for_defects=lambda ids: [{"id": "1", "defect_id": "D-1", "text": "Retest pending."}],
        fetch_histories_for_defects=lambda ids, **kwargs: {"D-1": {"data": [{"timestamp": "2026-10-10", "change_set": [{"field_name": "target_i_step_udf", "value": "NA05-27-07-460"}]}], "total_count": 1}},
    )
    refresh_octane_tickets_by_ids(source_db_path=database_path, ticket_ids=["D-1"], client=client, evidence_directory=tmp_path / "evidence")
    inputs, _records, audit = load_current_evidence_inputs(database_path, ["D-1"], set())
    assert len(inputs) == 1
    assert audit["canonical_source_verified"] is True
    connection = sqlite3.connect(database_path)
    connection.execute("UPDATE octane_defect_history_events SET new_value_text='changed-with-same-count'")
    connection.commit()
    connection.close()
    with pytest.raises(ValueError, match="history content"):
        load_current_evidence_inputs(database_path, ["D-1"], set())
    connection = sqlite3.connect(database_path)
    connection.execute("UPDATE octane_defect_history_events SET new_value_text='NA05-27-07-460'")
    connection.execute("UPDATE octane_defects SET comments='[]'")
    connection.commit()
    connection.close()
    with pytest.raises(ValueError, match="consistency"):
        load_current_evidence_inputs(database_path, ["D-1"], set())


def test_current_state_is_synchronized_in_excel_json_and_cluster_details(tmp_path) -> None:
    record = _record("D-1", phase="08-In Verification", lifecycle_status="Open / active")
    record.update({
        "source_evidence_status": "current_verified", "evidence_refreshed_at": "2026-10-20T02:50:00Z",
        "historical_shift_status": "documented", "current_postponement_status": "unclear",
        "repair_status": "verification_pending", "delivery_status": "planned",
        "current_state_summary": "Fix reported; delivery planned; retest pending.",
        "resolution_timing_analysis": "Latest plan recorded on 2026-09-21; CW51.",
        "resolution_version_analysis": "Latest recorded delivery plan is 27-07-475; not delivered.",
        "status_evidence": {"repair_status": ["C-1"]},
        "current_evidence_references": [{"id": "C-1", "date": "2026-10-10", "source": "comment", "text": "Retest pending."}],
        "assessment_warnings": ["Current delay requires review"],
    })
    import pytest
    with pytest.raises(ValueError, match="source.*audit"):
        build_merged_shift_tolerate_artifact_triple([record], output_directory=tmp_path)
    outputs = build_merged_shift_tolerate_artifact_triple([record], output_directory=tmp_path, source_evidence_audit={"requested_ticket_count": 1, "current_verified_ticket_count": 1, "canonical_source_verified": True, "unavailable_ticket_ids": []})
    payload = json.loads(outputs["knowledge_base_json"].read_text(encoding="utf-8"))
    assert payload["source_evidence_audit"]["current_verified_ticket_count"] == 1
    assert payload["catalog_filters"]["excluded_phases"] == ["05", "08"]
    assert payload["summary"]["ticket_count"] == 1
    assert payload["ticket_mappings"][0]["repair_status"] == "verification_pending"
    assert "27-07-475" in payload["ticket_mappings"][0]["resolution_plan_summary"]
    assert "27-07-475" in payload["ticket_mappings"][0]["resolution_version_summary"]
    assert "27-11-460" not in payload["ticket_mappings"][0]["resolution_version_summary"]
    workbook = load_workbook(outputs["triple_xlsx"], read_only=True, data_only=True)
    overview = list(workbook["00_总览"].iter_rows(values_only=True))
    assert any(row[0] == "HTML ticket filters" and "05" in str(row[1]) and "08" in str(row[1]) for row in overview)
    rows = workbook["01_Scenario_Library"].iter_rows(values_only=True)
    headers = list(next(rows))
    values = next(rows)
    assert values[headers.index("Current Postponement Status")] == "unclear"
    assert values[headers.index("Repair Evidence Status")] == "verification_pending"
    assert "C-1" in values[headers.index("Current Status Evidence References")]
    workbook.close()
    source = load_workbook(outputs["source_xlsx"], read_only=True, data_only=True)
    source_headers = list(next(source["06_全部明细"].iter_rows(values_only=True)))
    assert "Repair Evidence Status" in source_headers
    assert source_headers.index("Ticket Creation Time") == source_headers.index("Ticket ID") + 1
    source.close()
    plan = next(node for node in payload["decision_tree_nodes"] if node["node_type"] == "Future Resolution Plan")
    details = {item["label"]: item["value"] for item in plan["detail_fields"]}
    assert "verification_pending" in details["Current repair evidence"]
    assert "Source evidence coverage" in details


def test_detailed_catalog_exposes_ticket_phase_creation_and_filter_contributions() -> None:
    from backend.analytics.open_shift_scenario_triple import _detailed_future_resolution_catalog_payload

    records = [
        _record("D-KEEP", phase="04-In Progress"),
        _record("D-TEST", phase="05-In Testing"),
        _record("D-VERIFY", phase="08-In Verification"),
    ]
    records[0]["creation_time"] = "2026-01-01T00:00:00Z"
    scenarios = build_detailed_future_resolution_scenarios(records)
    trees, nodes = build_detailed_future_resolution_trees(scenarios)
    catalog = _detailed_future_resolution_catalog_payload(trees, nodes, scenarios)
    assert catalog["ticket_filters"]["excluded_phases"] == ["05", "08"]
    navigation = next(item for item in catalog["functions"] if item["service_function_id"] == "NAVIGATION")
    by_id = {item["representative_ticket_id"]: item for item in navigation["scenario_records"]}
    assert by_id["D-KEEP"]["ticket_phase"] == "04-In Progress"
    assert by_id["D-KEEP"]["creation_time"] == "2026-01-01T00:00:00Z"
    assert by_id["D-VERIFY"]["ticket_phase"] == "08-In Verification"
    assert by_id["D-KEEP"]["filter_detail_values"]["Resolution plan"]
    assert by_id["D-KEEP"]["filter_detail_groups"]
    plan = next(item for item in navigation["data_tree_nodes"] if item["node_type"] == "Future Resolution Plan")
    assert set(plan["ticket_ids"]) == {"D-KEEP", "D-TEST", "D-VERIFY"}


def test_detailed_catalog_filters_rebuild_nodes_counts_and_evidence() -> None:
    from pathlib import Path
    import shutil
    import subprocess
    import pytest
    from backend.analytics.open_shift_scenario_triple import _render_detailed_future_resolution_catalog_html

    if not shutil.which("node"):
        pytest.skip("Browser catalog behavior requires the repository Node runtime")
    records = [
        _record("D-KEEP", phase="04-In Progress"),
        _record("D-OLD", phase="04-In Progress"),
        _record("D-END", phase="07-In Pre-Verification"),
        _record("D-UNDATED", phase="04-In Progress"),
        _record("D-TEST", phase="05-In Testing", title="Software update failed", issue_summary="Software update failed"),
        _record("D-VERIFY", phase="08-In Verification", title="Audio has no sound", issue_summary="Audio has no sound"),
    ]
    for record in records:
        record["rca_root_cause"] = record["ticket_id"] + "_ONLY_RCA"
    records[0]["creation_time"] = "2026-01-01T00:00:00Z"
    records[1]["creation_time"] = "2026-01-01T00:30:00+01:00"
    records[2]["creation_time"] = "2026-01-01T23:59:59Z"
    records[3]["creation_time"] = ""
    scenarios = build_detailed_future_resolution_scenarios(records)
    trees, nodes = build_detailed_future_resolution_trees(scenarios)
    html = _render_detailed_future_resolution_catalog_html(trees, nodes, scenarios)
    script = r"""
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
const dom = new JSDOM(readFileSync(0, 'utf8'), { runScripts: 'dangerously' });
const document = dom.window.document;
const change = (element) => element.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
const selector = document.querySelector('#function-selector');
selector.value = 'NAVIGATION'; change(selector);
const phases = [...document.querySelectorAll('#ticket-phase-options input[type="checkbox"]')];
assert.ok(phases.length >= 4, 'phase checkboxes must exist');
assert.equal(phases.find(element => element.value === '05').checked, false);
assert.equal(phases.find(element => element.value === '08').checked, false);
const count = () => Number(document.querySelector('#meta-tickets').textContent);
const nodeCount = () => document.querySelectorAll('#node-rows tr').length;
const choosePlan = () => document.querySelector('svg g[aria-label^="Future Resolution Plan:"]').dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
assert.equal(count(), 4);
const defaultNodes = nodeCount();
choosePlan();
assert.ok(!document.querySelector('#node-detail').textContent.includes('D-TEST_ONLY_RCA'));
document.querySelector('[data-phase-action="all"]').click();
assert.equal(count(), 6);
assert.ok(nodeCount() > defaultNodes, 'restoring phases must restore pruned branches');
document.querySelector('#reset-ticket-filters').click();
assert.equal(count(), 4);
const from = document.querySelector('#ticket-created-from');
const through = document.querySelector('#ticket-created-through');
from.value = '2026-01-01'; change(from);
through.value = '2026-01-01'; change(through);
assert.equal(count(), 2, 'UTC dates must include both day boundaries and exclude undated/previous UTC day');
assert.equal(Number(document.querySelector('#summary-tickets').textContent), 2);
assert.equal(document.querySelectorAll('#scenario-rows tr').length, 2);
choosePlan();
const detail = document.querySelector('#node-detail').textContent;
assert.ok(detail.includes('D-KEEP_ONLY_RCA'));
assert.ok(!detail.includes('D-OLD_ONLY_RCA'));
assert.ok(!detail.includes('D-TEST_ONLY_RCA'));
assert.ok([...document.querySelectorAll('#node-detail details')].every(element => !element.open));
through.value = '2025-12-31'; change(through);
assert.equal(through.checkValidity(), false);
through.value = ''; change(through);
from.value = '2027-01-01'; change(from);
assert.equal(count(), 0);
assert.equal(document.querySelector('#meta-tickets').textContent, '0');
assert.equal(nodeCount(), 0);
assert.equal(document.querySelectorAll('#scenario-rows tr').length, 0);
assert.ok(document.querySelector('#diagram-scroll').textContent.includes('No tickets match'));
assert.ok(!document.querySelector('#node-detail').textContent.includes('ONLY_RCA'));
document.querySelector('#reset-ticket-filters').click();
assert.equal(count(), 4);
document.querySelector('[data-phase-action="none"]').click();
assert.equal(count(), 0);
document.querySelector('[data-phase-action="all"]').click();
assert.equal(count(), 6);
dom.window.close();
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        input=html, text=True, encoding="utf-8", capture_output=True,
        cwd=Path(__file__).resolve().parents[2], timeout=60,
    )
    assert result.returncode == 0, result.stderr


def test_current_evidence_cli_supports_unlocked_candidate_output_directory() -> None:
    from pathlib import Path
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "scripts/rebuild_shift_current_evidence.py", "--help"],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "--output-directory" in result.stdout


def test_merge_shift_tolerate_records_deduplicates_and_preserves_scope_and_lifecycle() -> None:
    process_record = _record(
        "D-OPEN",
        phase="04-In Progress",
        selection_scope="Strict Shift/Tolerate process",
        future_resolution_plan="Process tier: Formal Shift/Tolerate process\nHistory decision: 2026-10-10",
    )
    open_record = _record(
        "D-OPEN",
        phase="04-In Progress",
        selection_scope="Open Shift",
        future_resolution_plan="Target I-Step: NA05-27-11-460\nTarget week: CW47",
    )
    closed_record = _record(
        "D-CLOSED",
        phase="09-Concluded without action",
        lifecycle_status="Closed / Phase 09",
        selection_scope="Strict Shift/Tolerate process",
    )

    merged = merge_shift_tolerate_records(
        [process_record, closed_record],
        [open_record],
    )

    assert [record["ticket_id"] for record in merged] == ["D-CLOSED", "D-OPEN"]
    merged_open = next(record for record in merged if record["ticket_id"] == "D-OPEN")
    assert merged_open["ticket_lifecycle_status"] == "Open / active"
    assert merged_open["selection_scope"] == (
        "Open Shift; Strict Shift/Tolerate process"
    )
    assert "History decision: 2026-10-10" in str(
        merged_open["future_resolution_plan"]
    )
    assert "Target week: CW47" in str(merged_open["future_resolution_plan"])
    assert next(
        record for record in merged if record["ticket_id"] == "D-CLOSED"
    )["ticket_lifecycle_status"] == "Closed / Phase 09"


def test_detailed_future_resolution_tree_clusters_symptom_and_trigger_patterns() -> None:
    detailed_records = [
        _record(
            "D-OPEN",
            phase="04-In Progress",
            lifecycle_status="Open / active",
            selection_scope="Open Shift; Strict Shift/Tolerate process",
            issue_summary="Navigation guidance disappears after restart.",
            title="Guidance disappears after vehicle restart",
        ),
        _record(
            "D-CLOSED",
            phase="09-Concluded without action",
            lifecycle_status="Closed / Phase 09",
            selection_scope="Strict Shift/Tolerate process",
            issue_summary="Navigation guidance disappears after restart.",
            title="Guidance disappears after restart",
            future_resolution_plan="Target release: 27-11\nDecision date: 2026-10-10",
        ),
    ]

    scenarios = build_detailed_future_resolution_scenarios(detailed_records)
    trees, nodes = build_detailed_future_resolution_trees(scenarios)

    tree = next(tree for tree in trees if tree["service_function_id"] == "NAVIGATION")
    tree_nodes = [node for node in nodes if node["tree_id"] == tree["tree_id"]]
    root = next(node for node in tree_nodes if node["node_type"] == "Entry")
    issue_groups = [node for node in tree_nodes if node["node_type"] == "Issue Group"]
    symptom_nodes = [node for node in tree_nodes if node["node_type"] == "Symptom Pattern"]
    trigger_nodes = [node for node in tree_nodes if node["node_type"] == "Trigger Pattern"]
    plan_nodes = [
        node for node in tree_nodes if node["node_type"] == "Future Resolution Plan"
    ]

    assert len(issue_groups) == 1
    assert len(symptom_nodes) == 1
    assert len(trigger_nodes) == 1
    assert len(plan_nodes) == 1
    root_details = {item["label"]: item["value"] for item in root["detail_fields"]}
    assert root_details["Ticket coverage"] == "2 ticket(s)"
    assert "Ticket lifecycle summary" not in root_details
    assert "Observed symptom patterns" in symptom_nodes[0]["question_or_action"]
    assert "Observed trigger patterns" in trigger_nodes[0]["question_or_action"]
    plan = plan_nodes[0]
    assert plan["source_ticket_count"] == 2
    assert plan["ticket_ids"] == ["D-CLOSED", "D-OPEN"]
    details = {item["label"]: item["value"] for item in plan["detail_fields"]}
    assert details["Ticket coverage"] == "2 ticket(s)"
    assert "NA05-27-11-460" in details["Resolution plan"]
    assert "NA05-27-11-460" in details["Resolution version"]
    assert details["Root-cause evidence"] == "No recorded root-cause evidence."
    assert details["Solution / recovery evidence"] == "No recorded solution or recovery evidence."
    assert "Comparison unavailable" in details["Resolution verification"]
    assert "Future resolution plan" not in details
    assert "Raw ticket planning evidence" in {
        group["label"] for group in plan["detail_groups"]
    }


def test_merged_artifact_triple_keeps_ticket_lifecycle_in_excel_json_and_selected_node_html(
    tmp_path,
) -> None:
    records = merge_shift_tolerate_records(
        [
            _record(
                "D-CLOSED",
                phase="09-Concluded without action",
                lifecycle_status="Closed / Phase 09",
                selection_scope="Strict Shift/Tolerate process",
            )
        ],
        [
            _record(
                "D-OPEN",
                phase="04-In Progress",
                selection_scope="Open Shift",
            )
        ],
    )

    outputs = build_merged_shift_tolerate_artifact_triple(
        records,
        output_directory=tmp_path,
        source_snapshot="2026-10-15T07:59:30Z",
        raw_evidence=[],
    )

    workbook = load_workbook(outputs["source_xlsx"], read_only=True, data_only=True)
    headers = [
        cell.value
        for cell in next(workbook["06_全部明细"].iter_rows(min_row=1, max_row=1))
    ]
    assert "Ticket Lifecycle Status" in headers
    assert "Selection Scope" in headers
    assert "Resolution Verification Status" in headers
    assert "Post-target Similar Ticket Evidence" in headers
    scenario_workbook = load_workbook(
        outputs["triple_xlsx"],
        read_only=True,
        data_only=True,
    )
    scenario_sheet = scenario_workbook["01_Scenario_Library"]
    scenario_headers = [
        cell.value
        for cell in next(scenario_sheet.iter_rows(min_row=1, max_row=1))
    ]
    scenario_values = next(scenario_sheet.iter_rows(min_row=2, max_row=2, values_only=True))
    scenario_positions = {
        header: index for index, header in enumerate(scenario_headers)
    }
    assert scenario_positions["Ticket Creation Time"] == scenario_positions["Ticket ID"] + 1
    assert scenario_values[scenario_positions["Ticket Creation Time"]] == datetime(
        2026, 9, 18, 8, 30, 45
    )
    creation_cell = scenario_sheet.cell(
        row=2, column=scenario_positions["Ticket Creation Time"] + 1
    )
    assert creation_cell.is_date
    for header in (
        "Ticket ID",
        "Ticket Title",
        "Confidence Category",
        "Function Responsible",
    ):
        assert header in scenario_positions
    for header in (
        "Recorded Ticket Context",
        "Ticket Count",
        "Confidence Categories",
        "Rep Ticket ID",
        "Rep Title",
        "Source Software Versions",
        "Service Mapping Source",
        "Ticket IDs",
    ):
        assert header not in scenario_positions
    assert scenario_values[
        scenario_positions["LLM Postponement Reason Analyses"]
    ] == "Release timing is recorded."
    assert scenario_values[
        scenario_positions["LLM Resolution Timing Analyses"]
    ] == "Target week is CW47."
    assert scenario_values[
        scenario_positions["LLM Resolution Version Analyses"]
    ] == "Target I-Step is NA05-27-11-460."
    for header in (
        "Ticket Phase",
        "Source Software Version",
        "Source PU/I-Step",
        "Target I-Step",
        "Target PU/I-Step",
        "Shift-to-Set PU",
    ):
        assert header in scenario_positions
    scenario_rows = list(scenario_sheet.iter_rows(min_row=2, values_only=True))
    open_scenario = next(
        row
        for row in scenario_rows
        if row[scenario_positions["Ticket ID"]] == "D-OPEN"
    )
    assert open_scenario[scenario_positions["Ticket Title"]] == (
        "Navigation guidance disappears after restart"
    )
    assert open_scenario[scenario_positions["Function Responsible"]] == "Owner A"
    assert open_scenario[scenario_positions["Ticket Phase"]] == "04-In Progress"
    assert open_scenario[scenario_positions["Source PU/I-Step"]] == "27-07"
    assert open_scenario[scenario_positions["Target I-Step"]] == "NA05-27-11-460"
    assert open_scenario[scenario_positions["Target PU/I-Step"]] == "27-11"
    mapping_sheet = scenario_workbook["07_Ticket_Mapping"]
    mapping_headers = [
        cell.value
        for cell in next(mapping_sheet.iter_rows(min_row=1, max_row=1))
    ]
    mapping_positions = {
        header: index for index, header in enumerate(mapping_headers)
    }
    for header in (
        "Ticket Phase",
        "Ticket Lifecycle Status",
        "Selection Scope",
        "Source Software Version",
        "Source PU/I-Step",
        "Target I-Step",
        "Target PU/I-Step",
        "Shift-to-Set PU",
    ):
        assert header not in mapping_positions
    open_mapping = next(
        row
        for row in mapping_sheet.iter_rows(min_row=2, values_only=True)
        if row[mapping_positions["Ticket ID"]] == "D-OPEN"
    )
    assert open_mapping[mapping_positions["Ticket ID"]] == "D-OPEN"
    payload = json.loads(outputs["knowledge_base_json"].read_text(encoding="utf-8"))
    assert payload["artifact_type"] == "merged_shift_tolerate_future_resolution_evidence"
    assert payload["summary"]["ticket_count"] == 2
    assert {item["ticket_lifecycle_status"] for item in payload["ticket_mappings"]} == {
        "Open / active",
        "Closed / Phase 09",
    }
    html = outputs["catalog_html"].read_text(encoding="utf-8")
    assert "Selected node" in html
    assert "Ticket lifecycle" in html
    assert "Resolution plan" in html
    assert "raw.detail_fields" in html
    assert "Ticket detail branch" not in html
    assert "data_tree_display_limit" not in html
    assert "Raw ticket planning evidence" in html
    assert "Resolution verification" in html


def test_load_ticket_creation_times_reads_only_requested_tickets(tmp_path) -> None:
    import sqlite3

    from backend.analytics.open_shift_scenario_triple import load_ticket_creation_times

    database_path = tmp_path / "creation.db"
    connection = sqlite3.connect(database_path)
    connection.execute("CREATE TABLE octane_defects (defect_id TEXT, creation_time TEXT)")
    connection.executemany(
        "INSERT INTO octane_defects VALUES (?, ?)",
        [("D-OPEN", "2026-09-18T08:30:45Z"), ("D-OTHER", "2026-09-19T00:00:00Z")],
    )
    connection.commit()
    connection.close()

    assert load_ticket_creation_times(database_path, ["D-OPEN"]) == {
        "D-OPEN": "2026-09-18T08:30:45Z"
    }
    assert load_ticket_creation_times(database_path, []) == {}


def test_load_shift_full_records_from_source_workbook_restores_merge_fields(tmp_path) -> None:
    source_path = tmp_path / "open_shift.xlsx"
    from openpyxl import Workbook

    source = Workbook()
    sheet = source.active
    sheet.title = "06_全部明细"
    headers = [
        "Ticket ID",
        "Ticket 标题",
        "Phase",
        "Service Function",
        "Service Mapping Source",
        "Confidence Category",
        "Confidence Basis",
        "Source Software",
        "Source PU/I-Step",
        "Target I-Step",
        "Target PU/I-Step",
        "Shift-to-Set PU",
        "First Use/SOP PU",
        "Closed in Version",
        "SW-HRL Closed Version",
        "App Version",
        "Future Resolution Status",
        "Future Resolution Plan",
        "LLM 问题摘要",
        "LLM 延期原因分析",
        "LLM 解决时间分析",
        "LLM 解决版本分析",
        "LLM 延期/计划判断",
        "LLM 待核实",
        "Function Responsible",
        "Solution Cluster",
        "Product Areas",
        "Defect Category",
        "Assigned ECU",
        "LLM Source",
        "LLM Model",
    ]
    sheet.append(headers)
    sheet.append(
        [
            "D-OPEN",
            "Guidance disappears after restart",
            "04-In Progress",
            "Navigation",
            "Customer Scenario Context",
            "Confirmed PU Shift",
            "Target I-Step is later.",
            "idcevo-pu2707-27w01.1-1",
            "27-07",
            "NA05-27-11-460",
            "27-11",
            "27-11",
            "",
            "",
            "",
            "",
            "Recorded future resolution plan - not verified repair",
            "Target I-Step: NA05-27-11-460",
            "Navigation guidance disappears after restart.",
            "Release timing is recorded.",
            "Target week is CW47.",
            "Target I-Step is NA05-27-11-460.",
            "Future plan is recorded.",
            "Verify delivery.",
            "Owner A",
            "Navigation CN",
            "Provide Navigation 2.0",
            "Navigation",
            "IDCEVO-25",
            "llm",
            "fixture-model",
        ]
    )
    source.save(source_path)

    records = load_shift_full_records_from_source_workbook(source_path)

    assert len(records) == 1
    assert records[0]["service_function_id"] == "NAVIGATION"
    assert records[0]["ticket_lifecycle_status"] == "Open / active"
    assert records[0]["target_i_step"] == "NA05-27-11-460"
    assert records[0]["future_resolution_plan"] == "Target I-Step: NA05-27-11-460"


def test_assess_post_target_recurrence_marks_similar_later_ticket_as_unverified() -> None:
    source = _record(
        "D-SOURCE",
        phase="04-In Progress",
        issue_summary="Navigation guidance disappears after restart.",
        title="Navigation guidance disappears after restart",
    )
    source["target_i_step"] = "NA05-27-11-460"
    candidates = [
        {
            "ticket_id": "D-LATER",
            "title": "Navigation guidance disappears after restart",
            "software_version": "idcevo-pu2711-27w50.1-1",
            "solution_cluster": "Navigation CN",
            "product_areas": "Provide Navigation 2.0",
            "defect_category": "Navigation",
            "phase": "04-In Progress",
        }
    ]

    assessed = assess_post_target_recurrence([source], candidates)

    assert assessed[0]["resolution_verification_status"] == "recurrence_observed"
    assert assessed[0]["resolution_verification_summary"].startswith(
        "Recurrence observed"
    )
    assert assessed[0]["post_target_recurrence_candidates"] == [
        {
            "ticket_id": "D-LATER",
            "software_version": "idcevo-pu2711-27w50.1-1",
            "phase": "04-In Progress",
        }
    ]


def test_assess_post_target_recurrence_never_equates_absence_with_resolved() -> None:
    source = _record("D-SOURCE", phase="04-In Progress")
    source["target_i_step"] = "NA05-27-11-460"
    candidates = [
        {
            "ticket_id": "D-UNRELATED",
            "title": "Bluetooth call audio drops",
            "software_version": "idcevo-pu2711-27w50.1-1",
            "solution_cluster": "Navigation CN",
            "product_areas": "Provide Navigation 2.0",
            "defect_category": "Navigation",
            "phase": "04-In Progress",
        }
    ]

    assessed = assess_post_target_recurrence([source], candidates)

    assert assessed[0]["resolution_verification_status"] == "no_recurrence_evidence"
    assert "not a verified repair" in assessed[0]["resolution_verification_summary"]
    assert assessed[0]["post_target_recurrence_candidates"] == []


def test_assess_post_target_recurrence_marks_unparseable_target_as_unavailable() -> None:
    source = _record("D-SOURCE", phase="04-In Progress")
    source["target_i_step"] = "Target not recorded"
    source["target_release"] = ""
    source["shift_to_set_pu"] = ""
    source["first_use_sop_pu"] = ""
    source["closed_in_version"] = ""
    source["sw_hrl_closed_version"] = ""
    source["app_version"] = ""

    assessed = assess_post_target_recurrence([source], [])

    assert assessed[0]["resolution_verification_status"] == "comparison_unavailable"
    assert "target release" in assessed[0]["resolution_verification_summary"].casefold()
