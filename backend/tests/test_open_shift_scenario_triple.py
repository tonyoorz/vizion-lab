import json

from openpyxl import load_workbook

from backend.analytics.open_shift_scenario_triple import (
    FUTURE_RESOLUTION_STATUS,
    build_open_shift_artifact_triple,
    build_shift_full_records,
    build_shift_scenarios,
    load_open_shift_raw_evidence,
    load_open_shift_full_records,
)
from backend.analytics.open_ticket_postponement import (
    build_ticket_record,
    write_llm_input_jsonl,
)


def test_build_shift_scenarios_groups_same_service_and_symptom_without_claiming_repair() -> None:
    scenarios = build_shift_scenarios(
        [
            {
                "ticket_id": "D-1",
                "service_function_id": "NAVIGATION",
                "service_function": "Navigation",
                "service_mapping_source": "Customer Scenario Context",
                "issue_summary": "重启后导航引导消失。",
                "title": "Navigation guidance disappears after restart",
                "confidence_category": "Confirmed PU Shift",
                "confidence_basis": "Target I-Step is later than the reported software PU.",
                "future_resolution_plan": "Target I-Step: NA05-27-11-460",
                "function_responsible": "Owner A",
                "source_software_version": "idcevo-pu2707-27w01.1-1",
            },
            {
                "ticket_id": "D-2",
                "service_function_id": "NAVIGATION",
                "service_function": "Navigation",
                "service_mapping_source": "Customer Scenario Context",
                "issue_summary": "重启后导航引导消失。",
                "title": "Navigation route is not shown after reboot",
                "confidence_category": "Confirmed PU Shift",
                "confidence_basis": "Shift-to-Set is later than the reported software PU.",
                "future_resolution_plan": "Shift-to-Set PU: 27-11",
                "function_responsible": "Owner B",
                "source_software_version": "idcevo-pu2707-27w01.2-1",
            },
        ]
    )

    assert len(scenarios) == 1
    scenario = scenarios[0]
    assert scenario["ticket_count"] == 2
    assert scenario["service_function"] == "Navigation"
    assert scenario["future_resolution_status"] == (
        "Recorded future resolution plan - not verified repair"
    )
    assert scenario["ticket_ids"] == ["D-1", "D-2"]


def test_build_shift_scenarios_clusters_v12_equivalent_symptom_tokens() -> None:
    scenarios = build_shift_scenarios(
        [
            {
                "ticket_id": "D-ERROR-1",
                "service_function_id": "NAVIGATION",
                "service_function": "Navigation",
                "service_mapping_source": "Customer Scenario Context",
                "issue_summary": "导航路径计算错误。",
                "title": "Navigation route calculation error",
                "confidence_category": "Confirmed PU Shift",
                "confidence_basis": "Target I-Step is later.",
                "future_resolution_plan": "Target I-Step: NA05-27-11-460",
                "function_responsible": "Owner A",
            },
            {
                "ticket_id": "D-ERROR-2",
                "service_function_id": "NAVIGATION",
                "service_function": "Navigation",
                "service_mapping_source": "Customer Scenario Context",
                "issue_summary": "导航路线显示错误。",
                "title": "Navigation route display error",
                "confidence_category": "Confirmed PU Shift",
                "confidence_basis": "Shift-to-Set is later.",
                "future_resolution_plan": "Shift-to-Set PU: 27-11",
                "function_responsible": "Owner B",
            },
        ]
    )

    assert len(scenarios) == 1
    assert scenarios[0]["symptom_token"] == "error"
    assert scenarios[0]["problem_type"] == "navigation_location"


def test_build_shift_full_records_keeps_validated_llm_and_function_metadata() -> None:
    record = build_ticket_record(
        {
            "ticket_id": "D-NAV",
            "name": "Navigation guidance disappears after restart",
            "phase": "04-In Progress",
            "software_version": "idcevo-pu2607-26w15.1-1",
        },
        [
            {
                "field_name": "target_set_udf",
                "new_value_text": "27-07",
                "event_timestamp": "2026-10-10T08:00:00Z",
            }
        ],
    )

    full_records = build_shift_full_records(
        [record],
        {
            "D-NAV": {
                "source": "llm",
                "issue_summary": "重启后导航引导消失。",
                "postponement_assessment": "Shift-to-Set 指向 27-07。",
                "postponement_reason_analysis": "记录未说明具体延期原因，仅记录 Shift-to-Set 计划。",
                "resolution_timing_analysis": "未记录目标周或决定日期。",
                "resolution_version_analysis": "Shift-to-Set PU 为 27-07。",
                "review_focus": "核实集成版本。",
            }
        },
        {
            "D-NAV": {
                "solution_cluster": "Navigation CN",
                "product_areas": "Provide Navigation 2.0",
                "defect_category": "Navigation",
                "assigned_ecu": "IDCEVO-25",
                "function_responsible": "Owner A",
            }
        },
    )

    assert len(full_records) == 1
    full_record = full_records[0]
    assert full_record["service_function"] == "Navigation"
    assert full_record["issue_summary"] == "重启后导航引导消失。"
    assert full_record["future_resolution_status"] == FUTURE_RESOLUTION_STATUS
    assert "Shift-to-Set PU: 27-07" in full_record["future_resolution_plan"]
    assert full_record["postponement_reason_analysis"].startswith("记录未说明")
    assert full_record["resolution_timing_analysis"] == "未记录目标周或决定日期。"
    assert full_record["resolution_version_analysis"] == "Shift-to-Set PU 为 27-07。"


def test_build_open_shift_artifact_triple_writes_synchronized_v12_style_outputs(tmp_path) -> None:
    full_records = [
        {
            "ticket_id": "D-1",
            "title": "Navigation guidance disappears after restart",
            "phase": "04-In Progress",
            "service_function_id": "NAVIGATION",
            "service_function": "Navigation",
            "service_mapping_source": "Customer Scenario Context",
            "service_domain": "Navigation / Location",
            "confidence_category": "Confirmed PU Shift",
            "confidence_basis": "Target I-Step is later than the reported software PU.",
            "issue_summary": "重启后导航引导消失。",
            "postponement_assessment": "Target I-Step 指向 27-11。",
            "postponement_reason_analysis": "记录显示验证尚未完成。",
            "resolution_timing_analysis": "目标周为 CW47。",
            "resolution_version_analysis": "目标 I-Step 为 NA05-27-11-460。",
            "review_focus": "核实集成版本。",
            "future_resolution_status": FUTURE_RESOLUTION_STATUS,
            "future_resolution_plan": "Target I-Step: NA05-27-11-460",
            "source_software_version": "idcevo-pu2707-27w01.1-1",
            "source_release": "27-07",
            "target_i_step": "NA05-27-11-460",
            "target_release": "27-11",
            "shift_to_set_pu": "",
            "first_use_sop_pu": "",
            "closed_in_version": "",
            "sw_hrl_closed_version": "",
            "app_version": "",
            "function_responsible": "Owner A",
            "solution_cluster": "Navigation CN",
            "product_areas": "Provide Navigation 2.0",
            "defect_category": "Navigation",
            "assigned_ecu": "IDCEVO-25",
            "llm_source": "llm",
            "llm_model": "fixture-model",
            "description": "The navigation guidance disappears after restart.",
            "comments": "Target is 27-11.",
            "plan_evidence_signals": "Target I-Step: NA05-27-11-460",
        }
    ]

    outputs = build_open_shift_artifact_triple(
        full_records,
        output_directory=tmp_path,
        source_snapshot="2026-10-15T07:59:30Z",
        raw_evidence=[
            {
                "ticket_id": "D-1",
                "evidence_type": "Description",
                "timestamp": "",
                "actor": "",
                "field_name": "description",
                "old_value": "",
                "new_value": "",
                "content": "The navigation guidance disappears after restart.",
            },
            {
                "ticket_id": "D-1",
                "evidence_type": "Comment",
                "timestamp": "2026-10-10T01:00:00Z",
                "actor": "Owner A",
                "field_name": "comments",
                "old_value": "",
                "new_value": "",
                "content": "Target is 27-11.",
            },
            {
                "ticket_id": "D-1",
                "evidence_type": "History",
                "timestamp": "2026-10-10T02:00:00Z",
                "actor": "Owner A",
                "field_name": "target_set_udf",
                "old_value": "26-07",
                "new_value": "27-11",
                "content": "",
            },
        ],
    )

    assert all(path.exists() for path in outputs.values())
    source_workbook = load_workbook(outputs["source_xlsx"], read_only=True, data_only=True)
    assert source_workbook.sheetnames == ["00_总览", "06_全部明细", "08_证据原文"]
    source_headers = [
        cell.value
        for cell in next(source_workbook.worksheets[1].iter_rows(min_row=1, max_row=1))
    ]
    assert "LLM 延期原因分析" in source_headers
    assert "LLM 解决时间分析" in source_headers
    assert "LLM 解决版本分析" in source_headers
    assert source_workbook.worksheets[2].max_row - 1 == 3
    triple_workbook = load_workbook(outputs["triple_xlsx"], read_only=True, data_only=True)
    assert "01_Scenario_Library" in triple_workbook.sheetnames
    payload = json.loads(outputs["knowledge_base_json"].read_text(encoding="utf-8"))
    assert payload["schema_version"] == "open-shift-scenario-kb-v1"
    assert payload["summary"]["ticket_count"] == 1
    assert payload["scenarios"][0]["postponement_reason_analyses"] == [
        "记录显示验证尚未完成。"
    ]
    html = outputs["catalog_html"].read_text(encoding="utf-8")
    assert "Recorded future resolution plan - not verified repair" in html
    assert "记录显示验证尚未完成。" in html
    assert "<svg" in html
    assert "Future Resolution Plan" in html


def test_load_open_shift_full_records_reads_sqlite_and_validated_v2_summaries(tmp_path) -> None:
    db_path = tmp_path / "source.db"
    connection = __import__("sqlite3").connect(db_path)
    try:
        connection.executescript(
            """
            CREATE TABLE octane_defects (
                defect_id TEXT, name TEXT, phase TEXT, project TEXT, tproject TEXT,
                team TEXT, problem_finder_team TEXT, function_responsible TEXT,
                solution_responsible TEXT, creation_time TEXT, last_modified TEXT,
                software_version TEXT, description TEXT, comments TEXT,
                solution_cluster TEXT, product_areas TEXT, defect_category TEXT,
                assigned_ecu TEXT, ecu_to_modul TEXT
            );
            CREATE TABLE octane_defect_history_events (
                defect_id TEXT, field_name TEXT, old_value TEXT, new_value TEXT,
                old_value_text TEXT, new_value_text TEXT, event_timestamp TEXT,
                entry_index INTEGER, change_index INTEGER
            );
            """
        )
        connection.execute(
            "INSERT INTO octane_defects VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "D-LOAD", "Navigation guidance disappears", "04-In Progress", "IDCEVO", "IDCEVO",
                "FIT", "FIT", "Owner A", "bmw", "2026-01-01T00:00:00Z",
                "2026-10-10T00:00:00Z", "idcevo-pu2607-26w15.1-1", "Description", "[]",
                "Navigation CN", "Provide Navigation 2.0", "Navigation", "IDCEVO-25", "Maps",
            ),
        )
        connection.execute(
            "INSERT INTO octane_defect_history_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "D-LOAD", "target_set_udf", "", "27-07", "", "27-07",
                "2026-10-10T00:00:00Z", 1, 1,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    seed_record = build_ticket_record(
        {
            "ticket_id": "D-LOAD",
            "name": "Navigation guidance disappears",
            "phase": "04-In Progress",
            "project": "IDCEVO",
            "tproject": "IDCEVO",
            "team": "FIT",
            "problem_finder_team": "FIT",
            "function_responsible": "Owner A",
            "solution_responsible": "bmw",
            "creation_time": "2026-01-01T00:00:00Z",
            "last_modified": "2026-10-10T00:00:00Z",
            "software_version": "idcevo-pu2607-26w15.1-1",
            "description": "Description",
            "comments": "[]",
        },
        [
            {
                "field_name": "target_set_udf",
                "new_value_text": "27-07",
                "event_timestamp": "2026-10-10T00:00:00Z",
                "entry_index": 1,
                "change_index": 1,
            }
        ],
    )
    inputs_path = tmp_path / "inputs.jsonl"
    input_record = write_llm_input_jsonl([seed_record], inputs_path)[0]
    summaries_path = tmp_path / "summaries.jsonl"
    summaries_path.write_text(
        json.dumps(
            {
                "ticket_id": "D-LOAD",
                "input_fingerprint": input_record["input_fingerprint"],
                "source": "llm",
                "model": "fixture-model",
                "issue_summary": "导航引导消失。",
                "postponement_assessment": "Shift-to-Set 指向 27-07。",
                    "postponement_reason_analysis": "未记录具体延期原因。",
                    "resolution_timing_analysis": "未记录目标周。",
                    "resolution_version_analysis": "Shift-to-Set PU 为 27-07。",
                "review_focus": "核实集成版本。",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    records = load_open_shift_full_records(db_path, inputs_path, summaries_path)

    assert len(records) == 1
    assert records[0]["service_function"] == "Navigation"
    assert records[0]["issue_summary"] == "导航引导消失。"


def test_load_open_shift_raw_evidence_keeps_description_comments_and_history(tmp_path) -> None:
    db_path = tmp_path / "evidence.db"
    connection = __import__("sqlite3").connect(db_path)
    try:
        connection.executescript(
            """
            CREATE TABLE octane_defects (defect_id TEXT, description TEXT, comments TEXT);
            CREATE TABLE octane_defect_history_events (
                defect_id TEXT, event_timestamp TEXT, user_name TEXT, field_name TEXT,
                old_value TEXT, new_value TEXT, old_value_text TEXT, new_value_text TEXT,
                entry_index INTEGER, change_index INTEGER
            );
            """
        )
        connection.execute(
            "INSERT INTO octane_defects VALUES (?, ?, ?)",
            (
                "D-EVIDENCE",
                "Original description",
                json.dumps(
                    [
                        {
                            "text": "Original comment",
                            "creation_time": "2026-10-10T01:00:00Z",
                            "author": {"name": "Reviewer"},
                        }
                    ]
                ),
            ),
        )
        connection.execute(
            "INSERT INTO octane_defect_history_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "D-EVIDENCE", "2026-10-10T02:00:00Z", "Reviewer", "target_set_udf",
                "26-07", "27-07", "26-07", "27-07", 1, 1,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    evidence = load_open_shift_raw_evidence(db_path, ["D-EVIDENCE"])

    assert [row["evidence_type"] for row in evidence] == [
        "Description",
        "Comment",
        "History",
    ]
    assert evidence[0]["content"] == "Original description"
    assert evidence[1]["content"] == "Original comment"
    assert evidence[2]["field_name"] == "target_set_udf"
    assert evidence[2]["new_value"] == "27-07"