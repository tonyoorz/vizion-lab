import csv
import importlib
import json
import sqlite3

import pytest


HK_VIN = "WBA32JM0X0HY52877"
OTHER_HK_VIN = "WBA31HR040HY42964"
CN_VIN = "LBV61FX0XSM627118"
TW_VIN = "WBA81HM020HY56858"
TW_OVERRIDE_VIN = "WBA21HY030HY47120"
AIDA = "Use Speech operation [01.04.02.01.01.05]"


def report_module():
    return importlib.import_module("backend.analytics.hk_platform_output_report")


def sheet_records(workbook, title, **filters):
    values = iter(workbook[title].values)
    columns = next(values)
    records = [dict(zip(columns, row)) for row in values]
    return [row for row in records if all(row.get(key) == value for key, value in filters.items())]


@pytest.fixture
def source_db(tmp_path):
    path = tmp_path / "source.db"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE octane_manual_runs (
            mr_id TEXT PRIMARY KEY, test_id TEXT, test_name TEXT, status TEXT,
            testplatformid TEXT, raw_json TEXT, finished TEXT, started TEXT,
            creation_time TEXT, year INTEGER, top_aida TEXT, defect_id TEXT
        );
        CREATE TABLE octane_defects (
            defect_id TEXT PRIMARY KEY, name TEXT, vin TEXT, creation_time TEXT,
            year INTEGER, top_aida TEXT, market TEXT, detected_by TEXT,
            phase TEXT, problem_severity TEXT
        );
    """)
    runs = [
        ("R1", "T1", "Speech", "Passed", HK_VIN, "{}", "2025-03-01T10:00:00Z", "", "2025-03-01T09:00:00Z", 2025, AIDA, ""),
        ("R2", "T1", "Speech", "Failed", "HY52877", "{}", "", "2026-03-01T10:00:00Z", "2026-03-01T09:00:00Z", 2026, AIDA, "D1"),
        ("R3", "T1", "Speech", "Passed", "", "{}", "2025-04-01T10:00:00Z", "", "2025-04-01T09:00:00Z", 2025, AIDA, "D1"),
        ("R4", "T1", "Speech", "Passed", CN_VIN, "{}", "2025-04-01T10:00:00Z", "", "2025-04-01T09:00:00Z", 2025, AIDA, "D1"),
        ("R5", "T3", "Planned only", "Planned", HK_VIN, "{}", "", "", "2026-04-01T09:00:00Z", 2026, AIDA, ""),
        ("R6", "T1", "Old execution", "Passed", HK_VIN, "{}", "2020-04-01T10:00:00Z", "", "2025-04-01T09:00:00Z", 2025, AIDA, ""),
        ("R7", "T2", "Raw VIN", "Passed", "", json.dumps({"testplatformid_udf": HK_VIN}), "2026-04-01T10:00:00Z", "", "2026-04-01T09:00:00Z", 2026, AIDA, ""),
        ("R8", "T2", "Second vehicle", "Passed", OTHER_HK_VIN, "{}", "2026-05-01T10:00:00Z", "", "2026-05-01T09:00:00Z", 2026, "FL " + AIDA, ""),
    ]
    connection.executemany("INSERT INTO octane_manual_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", runs)
    connection.executemany("INSERT INTO octane_defects VALUES (?,?,?,?,?,?,?,?,?,?)", [
        ("D1", "Speech issue", HK_VIN + "; " + OTHER_HK_VIN, "2025-03-01T10:00:00Z", 2025, AIDA, "CN", "Free test", "06-Concluded", "High"),
        ("D2", "Speech issue 2", "HY52877", "2026-03-01T10:00:00Z", 2026, "FL " + AIDA, "CN", "Test case", "03-In Analysis", "Medium"),
        ("D3", "Old issue", HK_VIN, "2024-03-01T10:00:00Z", 2025, AIDA, "HK", "Free test", "06-Concluded", "High"),
        ("D4", "Other vehicle", CN_VIN, "2025-03-01T10:00:00Z", 2025, AIDA, "HK", "Free test", "06-Concluded", "High"),
    ])
    connection.commit()
    connection.close()
    return path


def test_vin_matching_uses_full_vin_or_bounded_known_suffix():
    module = report_module()
    assert module.match_vins("vin: hy52877 / " + OTHER_HK_VIN.lower(), [HK_VIN, OTHER_HK_VIN]) == [HK_VIN, OTHER_HK_VIN]
    assert module.match_vins("XHY52877X; " + CN_VIN, [HK_VIN]) == []


def test_uses_direct_run_vehicle_not_linked_defect_vehicle(source_db):
    payload = report_module().build_report(source_db)
    assert {row["run_id"] for row in payload["runs"]} == {"R1", "R2", "R5", "R7", "R8"}
    assert {row["defect_id"] for row in payload["defects"]} == {"D1", "D2"}
    assert payload["overview"]["executed_runs"] == 4
    assert payload["overview"]["planned_runs"] == 1
    assert payload["overview"]["executed_testcases"] == 2
    assert payload["overview"]["defect_count"] == 2


def test_calendar_dates_override_release_year_and_keep_fallbacks_visible(source_db):
    payload = report_module().build_report(source_db)
    runs = {row["run_id"]: row for row in payload["runs"]}
    assert "R6" not in runs
    assert runs["R2"]["date_basis"] == "started"
    assert runs["R5"]["date_basis"] == "creation_time"
    assert runs["R7"]["vin_source"] == "raw_json.testplatformid_udf"


def test_aida_code_groups_name_variants_without_counting_multivin_twice(source_db):
    payload = report_module().build_report(source_db)
    rows = payload["aida_summary"]
    assert len(rows) == 1
    assert rows[0]["aida_code"] == "01.04.02.01.01.05"
    assert rows[0]["executed_runs"] == 4
    assert rows[0]["defect_count"] == 2
    assert rows[0]["executed_testcases"] == 2
    assert len(payload["defects"][0]["matched_vins"]) == 2


def test_source_is_read_only_and_hy52877_override_is_local(source_db):
    before = source_db.read_bytes()
    report_module().build_report(source_db)
    assert source_db.read_bytes() == before
    connection = sqlite3.connect(source_db)
    assert connection.execute("SELECT market FROM octane_defects WHERE defect_id='D1'").fetchone()[0] == "CN"
    connection.close()


def test_team_scope_filters_runs_catalog_and_defect_finders(source_db):
    connection = sqlite3.connect(source_db)
    connection.executescript("""
        ALTER TABLE octane_manual_runs ADD COLUMN team TEXT DEFAULT 'DTSV_China';
        ALTER TABLE octane_defects ADD COLUMN problem_finder_team TEXT DEFAULT 'DTSV China';
        CREATE TABLE octane_testcases (test_id TEXT, test_name TEXT, scope_team TEXT, scope_release TEXT);
        INSERT INTO octane_testcases VALUES ('T1', '[HK] Speech', 'ALL', '2025');
        INSERT INTO octane_testcases VALUES ('EXTERNAL', '[HK] External', 'ALL', '2025');
        UPDATE octane_manual_runs SET team='DTSV_Germany' WHERE mr_id='R2';
        UPDATE octane_manual_runs SET team='' WHERE mr_id='R7';
        UPDATE octane_manual_runs SET team='DTSV_China_Support', test_id='EXTERNAL', test_name='[HK] External' WHERE mr_id='R8';
        UPDATE octane_defects SET problem_finder_team='DTSV_Germany' WHERE defect_id='D2';
    """)
    connection.commit()
    connection.close()
    before = source_db.read_bytes()

    payload = report_module().build_report(source_db, team_name="DTSV_China")

    assert {row["run_id"] for row in payload["runs"]} == {"R1", "R5"}
    assert {row["defect_id"] for row in payload["defects"]} == {"D1"}
    assert {row["run_id"] for row in payload["hk_testcase_analysis"]["runs"]} == {"R1", "R3", "R4"}
    assert {row["test_id"] for row in payload["hk_testcase_analysis"]["cases"]} == {"T1"}
    assert payload["scope"]["team_name"] == "DTSV_China"
    assert all(row["team"] == "DTSV_China" for row in payload["runs"])
    assert source_db.read_bytes() == before


@pytest.fixture
def regional_source(source_db, tmp_path):
    connection = sqlite3.connect(source_db)
    connection.executescript("""
        ALTER TABLE octane_manual_runs ADD COLUMN team TEXT DEFAULT 'DTSV_China';
        ALTER TABLE octane_defects ADD COLUMN problem_finder_team TEXT DEFAULT 'DTSV_China';
        CREATE TABLE octane_testcases (test_id TEXT, test_name TEXT, scope_team TEXT, scope_release TEXT);
        INSERT INTO octane_testcases VALUES ('T1', '[HK] Shared speech', 'ALL', '2025');
        INSERT INTO octane_testcases VALUES ('T-TW', '[TW] Shared speech', 'ALL', '2025');
    """)
    connection.executemany("""INSERT INTO octane_manual_runs
        (mr_id,test_id,test_name,status,testplatformid,raw_json,finished,year,top_aida,team)
        VALUES (?,?,?,?,?,?,?,?,?,?)""", [
        ("R9", "T-TW", "Shared speech without regional title", "Failed", TW_VIN, "{}", "2025-06-01T10:00:00Z", 2025, AIDA, "DTSV_China"),
        ("R10", "T-RACK", "[TW] Rack speech", "Blocked", "TAIWANRACK", "{}", "2026-06-01T10:00:00Z", 2026, AIDA, "DTSV_China"),
        ("R11", "T-EXTERNAL", "External speech", "Passed", TW_OVERRIDE_VIN, "{}", "2025-06-01T10:00:00Z", 2025, AIDA, "DTSV_Germany"),
        ("R12", "T-UNKNOWN", "Unmapped speech", "Passed", "WBA11LE030HY56855", "{}", "2025-06-01T10:00:00Z", 2025, AIDA, "DTSV_China"),
        ("R14", "T-MIXED", "Mixed region speech", "Passed", CN_VIN + ";" + HK_VIN, "{}", "2025-06-01T10:00:00Z", 2025, AIDA, "DTSV_China"),
    ])
    connection.executemany("""INSERT INTO octane_defects
        (defect_id,name,vin,creation_time,year,top_aida,market,phase,problem_finder_team)
        VALUES (?,?,?,?,?,?,?,?,?)""", [
        ("D5", "TW issue", TW_VIN, "2025-06-01T10:00:00Z", 2025, AIDA, "TW", "06-Concluded", "DTSV_China"),
        ("D6", "Old CN mapping now confirmed TW", TW_OVERRIDE_VIN, "2025-06-01T10:00:00Z", 2025, AIDA, "CN", "09-Rejected", "DTSV_China"),
        ("D7", "Mixed HK and CN issue", CN_VIN + ";" + HK_VIN, "2025-06-01T10:00:00Z", 2025, AIDA, "CN", "03-In Analysis", "DTSV_China"),
        ("D8", "CN issue", CN_VIN, "2026-06-01T10:00:00Z", 2026, AIDA, "CN", "09-Rejected", "DTSV_China"),
        ("D9", "TW external issue", TW_VIN, "2025-06-01T10:00:00Z", 2025, AIDA, "TW", "06-Concluded", "DTSV_Germany"),
        ("D10", "Unmapped issue with CN market", "WBA11LE030HY56855", "2025-06-01T10:00:00Z", 2025, AIDA, "CN", "06-Concluded", "DTSV_China"),
        ("D11", "Mixed TW and CN issue", CN_VIN + ";" + TW_VIN, "2025-06-01T10:00:00Z", 2025, AIDA, "TW;CN", "06-Concluded", "DTSV_China"),
    ])
    connection.commit()
    connection.close()
    mapping = tmp_path / "vin_markets.json"
    mapping.write_text(json.dumps([
        {"vin_prefix": CN_VIN, "market": "CN", "project": "IDCEVO25"},
        {"vin_prefix": HK_VIN, "market": "CN", "project": "IDCEVO25"},
        {"vin_prefix": TW_OVERRIDE_VIN, "market": "CN", "project": "IDCEVO25"},
        {"vin_prefix": "WBA11LE030HY56855", "market": "", "project": "IDCEVO25"},
        {"vin_prefix": "WBA11EE0705232179", "market": "DE", "project": "MGU_02_L"},
    ]), encoding="utf-8")
    fleet = tmp_path / "fleet.json"
    fleet.write_text(json.dumps({"fetched_at": "2026-09-04T02:46:05Z", "platforms": [
        {"ids": {"vin": TW_VIN, "vNumber": "TAIWANRACK", "fleet": ["DTSV_China_R"]}, "fa": {"current": {"modelSeries": "U11"}}},
        {"ids": {"vin": CN_VIN, "vNumber": "123456", "fleet": ["DTSV_China_C"]}, "fa": {"current": {"modelSeries": "G45"}}},
    ]}), encoding="utf-8")
    return source_db, fleet, mapping


def test_regional_inventory_prioritizes_confirmations_and_requires_positive_cn_mapping(regional_source):
    source_db, fleet, mapping = regional_source
    inventory = report_module().load_regional_inventory(fleet, mapping)
    by_vin = {row["vin"]: row for row in inventory}

    assert by_vin[HK_VIN]["region"] == "HK"
    assert by_vin[TW_OVERRIDE_VIN]["region"] == "TW"
    assert by_vin[CN_VIN]["region"] == "CN"
    assert by_vin["WBA11LE030HY56855"]["region"] == "Unknown"
    assert by_vin["WBA11EE0705232179"]["region"] == "DE"
    assert sum(row["region"] == "TW" and row["evidence"] == "user_confirmed" for row in inventory) == 10


@pytest.mark.parametrize("region,run_ids,defect_ids", [
    ("TW", {"R9", "R10"}, {"D5", "D6", "D11"}),
    ("CN", {"R4"}, {"D4", "D8"}),
])
def test_regional_reports_use_team_platform_evidence_not_title_or_market_guesses(regional_source, region, run_ids, defect_ids):
    module = report_module()
    source_db, fleet, mapping = regional_source
    before = source_db.read_bytes()

    payload = module.build_report(source_db, region=region, fleet_snapshot=fleet, market_mapping=mapping)

    assert payload["scope"]["region"] == region
    assert payload["scope"]["team_name"] == "DTSV_China"
    assert {row["run_id"] for row in payload["runs"]} == run_ids
    assert {row["defect_id"] for row in payload["defects"]} == defect_ids
    primary_ids = run_ids if region == "TW" else {"R5", "R7", "R8", "R12", "R14"}
    assert {row["run_id"] for row in payload["hk_testcase_analysis"]["runs"]} == primary_ids
    assert payload["hk_testcase_analysis"]["scope"]["classification"] == ("explicit_tw_title" if region == "TW" else "dtsv_china_non_hk_tw_testcase")
    assert source_db.read_bytes() == before
    refreshed = module.refresh_platform_types(payload, module.load_v_number_mapping(fleet))
    assert {row["run_id"] for row in refreshed["runs"]} == run_ids
    assert module.build_report_views(refreshed)["hk_overview"]["defect_count"] == len(defect_ids)


def test_mainland_case_scope_joins_non_hk_tw_team_testcases_without_requiring_platform(regional_source, tmp_path):
    from openpyxl import load_workbook

    source_db, fleet, mapping = regional_source
    connection = sqlite3.connect(source_db)
    connection.execute("INSERT INTO octane_testcases VALUES ('T-CN','[CN] Speech','ALL','2025')")
    connection.execute("INSERT INTO octane_testcases VALUES ('T-CN-CATALOG','Mainland China catalog only','DTSV_China','2025')")
    connection.executemany("""INSERT INTO octane_manual_runs
        (mr_id,test_id,test_name,status,testplatformid,finished,year,team)
        VALUES (?,?,?,?,?,?,?,?)""", [
        ("R-CN1", "T-CN", "Unmarked repeat of CN case", "Passed", "", "2026-07-01T10:00:00Z", 2026, "DTSV_China"),
        ("R-CN2", "T-UNMARKED", "No regional title or platform", "Failed", "", "2026-07-01T10:00:00Z", 2026, "DTSV_China"),
        ("R-CN3", "T-HK-RUN", "[HK] Must be excluded", "Passed", CN_VIN, "2026-07-01T10:00:00Z", 2026, "DTSV_China"),
        ("R-CN4", "T-HK-RUN", "Unmarked repeat of HK case", "Passed", "", "2026-07-01T10:00:00Z", 2026, "DTSV_China"),
        ("R-CN5", "T-CN", "External team", "Passed", CN_VIN, "2026-07-01T10:00:00Z", 2026, "DTSV_Germany"),
        ("R-CN6", "T-OLD", "No period execution", "Passed", "", "2020-07-01T10:00:00Z", 2026, "DTSV_China"),
    ])
    connection.commit()
    connection.close()
    before = source_db.read_bytes()

    payload = report_module().build_report(source_db, region="CN", fleet_snapshot=fleet, market_mapping=mapping)

    analysis = payload["hk_testcase_analysis"]
    assert analysis["scope"]["classification"] == "dtsv_china_non_hk_tw_testcase"
    assert {row["test_id"] for row in analysis["cases"]} == {"T2", "T3", "T-UNKNOWN", "T-MIXED", "T-CN", "T-UNMARKED", "T-OLD", "T-CN-CATALOG"}
    assert {row["run_id"] for row in analysis["runs"]} == {"R5", "R7", "R8", "R12", "R14", "R-CN1", "R-CN2"}
    assert set(analysis["excluded_regional_testcase_ids"]) == {"T1", "T-TW", "T-RACK", "T-HK-RUN"}
    cases = {row["test_id"]: row for row in analysis["cases"]}
    assert cases["T-CN"]["testcase_scope_basis"] == "explicit_cn_title"
    assert cases["T-UNMARKED"]["testcase_scope_basis"] == "team_non_hk_tw_unmarked"
    assert analysis["scope"]["physical_execution_location_proven"] is False
    records = {row["run_id"]: row for row in analysis["runs"]}
    assert records["R-CN1"]["matched_vins"] == []
    assert records["R14"]["matched_vins"] == []
    assert {row["run_id"] for row in payload["runs"]} == {"R4", "R-CN3"}
    assert {row["defect_id"] for row in payload["defects"]} == {"D4", "D8"}
    assert source_db.read_bytes() == before
    module = report_module()
    refreshed = module.refresh_platform_types(payload, module.load_v_number_mapping(fleet))
    outputs = module.write_report(refreshed, tmp_path / "cn_relation_report")
    assert refreshed["report_views"]["hk_overview"]["run_records"] == 7
    assert "non-HK/TW" in refreshed["report_views"]["hk_overview"]["scope"]
    workbook = load_workbook(outputs["excel"], read_only=True)
    assert workbook["CN Cases"].max_row == 9
    assert len(sheet_records(workbook, "MR Details", in_testcase_cohort=True)) == 7
    assert len(sheet_records(workbook, "MR Details", in_vin_scope=True)) == 2
    workbook.close()
    markdown = outputs["markdown"].read_text(encoding="utf-8")
    assert "8 distinct testcase IDs" in markdown
    assert "7 related MR records" in markdown
    assert "strict CN-platform" in markdown
    assert "testcase_scope_basis" in markdown


def test_tw_case_cohort_follows_testcase_to_mr_links_like_hk_without_requiring_platform(regional_source, tmp_path):
    from openpyxl import load_workbook

    source_db, fleet, mapping = regional_source
    connection = sqlite3.connect(source_db)
    connection.execute("INSERT INTO octane_testcases VALUES ('T-TW-CATALOG-ONLY','[TW] No period execution','DTSV_China','2025')")
    connection.executemany("""INSERT INTO octane_manual_runs
        (mr_id,test_id,test_name,status,testplatformid,finished,year,team)
        VALUES (?,?,?,?,?,?,?,?)""", [
        ("R15", "T-TW-RUN-ONLY", "[Taiwan] Run-title-only case", "Planned", "", "2026-07-01T10:00:00Z", 2026, "DTSV_China"),
        ("R16", "T-TW", "Unmarked repeat of catalog TW case", "Passed", "", "2026-07-01T10:00:00Z", 2026, "DTSV_China"),
        ("R17", "T-TW", "Same TW case on HK platform", "Failed", HK_VIN, "2026-07-01T10:00:00Z", 2026, "DTSV_China"),
        ("R18", "T-TW", "External team must stay excluded", "Passed", TW_VIN, "2026-07-01T10:00:00Z", 2026, "DTSV_Germany"),
        ("R19", "T-TW", "Outside event year", "Passed", TW_VIN, "2020-07-01T10:00:00Z", 2026, "DTSV_China"),
    ])
    connection.commit()
    connection.close()
    before = source_db.read_bytes()

    payload = report_module().build_report(source_db, region="TW", fleet_snapshot=fleet, market_mapping=mapping)

    analysis = payload["hk_testcase_analysis"]
    assert {row["test_id"] for row in analysis["cases"]} == {"T-TW", "T-RACK", "T-TW-RUN-ONLY", "T-TW-CATALOG-ONLY"}
    assert {row["run_id"] for row in analysis["runs"]} == {"R9", "R10", "R15", "R16", "R17"}
    assert {row["run_id"] for row in payload["runs"]} == {"R9", "R10"}
    assert analysis["scope"]["classification"] == "explicit_tw_title"
    assert analysis["overview"]["executed_runs"] == 3
    assert analysis["overview"]["planned_runs"] == 1
    assert analysis["overview"]["cases_without_period_records"] == 1
    records = {row["run_id"]: row for row in analysis["runs"]}
    assert records["R16"]["matched_vins"] == []
    assert records["R17"]["matched_vins"] == []
    assert source_db.read_bytes() == before
    module = report_module()
    refreshed = module.refresh_platform_types(payload, module.load_v_number_mapping(fleet))
    outputs = module.write_report(refreshed, tmp_path / "tw_relation_report")
    assert refreshed["report_views"]["hk_overview"]["run_records"] == 5
    assert "TW-title testcase" in refreshed["report_views"]["hk_overview"]["scope"]
    workbook = load_workbook(outputs["excel"], read_only=True)
    assert workbook["TW Cases"].max_row == 5
    assert len(sheet_records(workbook, "MR Details", in_testcase_cohort=True)) == 5
    assert len(sheet_records(workbook, "MR Details", in_vin_scope=True)) == 2
    workbook.close()
    markdown = outputs["markdown"].read_text(encoding="utf-8")
    assert "4 distinct testcase IDs" in markdown
    assert "5 related MR records" in markdown
    assert "strict TW-platform" in markdown
    assert "explicit_tw_title" in markdown


@pytest.mark.parametrize("alias", ["CHUXIN", "TAIWANRACK"])
def test_mainland_excludes_mixed_full_vin_and_regional_v_number_aliases(regional_source, alias):
    source_db, fleet, mapping = regional_source
    connection = sqlite3.connect(source_db)
    identifier = CN_VIN + ";" + alias
    connection.execute("""INSERT INTO octane_manual_runs
        (mr_id,test_id,test_name,status,testplatformid,finished,year,team)
        VALUES ('R-MIXED-ALIAS','T-MIXED-ALIAS','Mixed platform','Passed',?,'2025-06-01T10:00:00Z',2025,'DTSV_China')""", (identifier,))
    connection.execute("""INSERT INTO octane_defects
        (defect_id,name,vin,creation_time,year,market,problem_finder_team)
        VALUES ('D-MIXED-ALIAS','Mixed platform',?,'2025-06-01T10:00:00Z',2025,'CN','DTSV_China')""", (identifier,))
    connection.commit()
    connection.close()

    payload = report_module().build_report(source_db, region="CN", fleet_snapshot=fleet, market_mapping=mapping)

    assert "R-MIXED-ALIAS" not in {row["run_id"] for row in payload["runs"]}
    assert "D-MIXED-ALIAS" not in {row["defect_id"] for row in payload["defects"]}


@pytest.mark.parametrize("region,run_ids", [("TW", {"R9", "R10"}), ("CN", {"R4"})])
def test_native_run_team_recovers_missing_column_without_overriding_current_team(regional_source, region, run_ids):
    source_db, fleet, mapping = regional_source
    connection = sqlite3.connect(source_db)
    native_team = json.dumps({"run_team_000_udf": {"type": "team", "id": "59139", "name": "DTSV_China"}})
    connection.execute("UPDATE octane_manual_runs SET team=NULL,raw_json=? WHERE mr_id IN ('R9','R10','R4')", (native_team,))
    connection.execute("UPDATE octane_manual_runs SET raw_json=? WHERE mr_id='R11'", (native_team,))
    if region == "CN":
        connection.execute("UPDATE octane_manual_runs SET team=NULL,raw_json=? WHERE mr_id='R12'", (native_team,))
        connection.execute("INSERT INTO octane_testcases VALUES ('T-UNKNOWN','[CN] Native team case','ALL','2025')")
    connection.commit()
    connection.close()
    before = source_db.read_bytes()

    payload = report_module().build_report(source_db, region=region, fleet_snapshot=fleet, market_mapping=mapping)

    assert {row["run_id"] for row in payload["runs"]} == run_ids
    assert all(row["team"] == "DTSV_China" and row["team_source"] == "raw_json.run_team_000_udf" for row in payload["runs"])
    if region == "TW":
        assert all(row["team_source"] == "raw_json.run_team_000_udf" for row in payload["hk_testcase_analysis"]["runs"])
    else:
        records = {row["run_id"]: row for row in payload["hk_testcase_analysis"]["runs"]}
        assert records["R12"]["team_source"] == "raw_json.run_team_000_udf"
        assert "R4" not in records
    assert all(row["catalog_record_count"] == 1 for row in payload["hk_testcase_analysis"]["cases"] if row["test_id"] in {"T-TW", "T-UNKNOWN"})
    assert source_db.read_bytes() == before


@pytest.mark.parametrize("region", ["TW", "CN"])
def test_regional_exports_have_matching_names_labels_and_record_parity(regional_source, tmp_path, region):
    from openpyxl import load_workbook

    module = report_module()
    source_db, fleet, mapping = regional_source
    payload = module.build_report(source_db, region=region, fleet_snapshot=fleet, market_mapping=mapping)
    payload = module.refresh_platform_types(payload, module.load_v_number_mapping(fleet))

    paths = module.write_report(payload, tmp_path / region)

    assert paths["html"].stem == region + "_Test_Output_2025_2026"
    label = "Mainland China" if region == "CN" else "TW"
    html = paths["html"].read_text(encoding="utf-8")
    assert "<h1>" + label + " Test Output</h1>" in html
    assert label + " Testcases &amp; Defects" in html
    assert region + "_Test_Output_2025_2026.xlsx" in html
    assert "HK-title" not in html.split('<script id="report-data"')[0]
    exported = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert exported["overview"] == payload["overview"]
    assert exported["scope"]["team_name"] == "DTSV_China"
    assert "not measured HK availability" not in json.dumps(exported["capacity_insights"]["facts"])
    assert "local HK-title" not in "\n".join(exported["capacity_insights"]["limits"])
    assert "approved HK productive" not in "\n".join(exported["capacity_insights"]["limits"])
    workbook = load_workbook(paths["excel"], read_only=True)
    assert len(sheet_records(workbook, "MR Details", in_testcase_cohort=True)) == len(payload["hk_testcase_analysis"]["runs"])
    assert workbook["Defects"].max_row == len(payload["defects"]) + 1
    workbook.close()
    with (paths["json"].parent / "executions.csv").open(encoding="utf-8-sig", newline="") as handle:
        assert sum(row["in_testcase_cohort"] == "True" for row in csv.DictReader(handle)) == len(payload["hk_testcase_analysis"]["runs"])
    markdown = paths["markdown"].read_text(encoding="utf-8")
    assert "# " + label + " Test Output Analysis" in markdown
    assert "DTSV_China" in markdown
    assert "Screenshot-confirmed six vehicles" not in markdown


def test_regional_cli_builds_a_new_local_report_with_explicit_mapping(regional_source, tmp_path, monkeypatch, capsys):
    import sys

    source_db, fleet, mapping = regional_source
    output = tmp_path / "tw_report"
    monkeypatch.setattr(sys, "argv", ["report", "--region", "TW", "--db", str(source_db),
                                    "--fleet-snapshot", str(fleet), "--market-mapping", str(mapping), "--output", str(output)])

    report_module().main()

    summary = json.loads(capsys.readouterr().out)
    assert summary["overview"]["defect_count"] == 3
    assert (output / "TW_Test_Output_2025_2026.json").exists()
    assert summary["source_unchanged_at_original_data_build"] is True
    assert not (output / "HK_Test_Output_2025_2026.json").exists()


@pytest.mark.parametrize("region", ["TW", "CN"])
def test_tw_frozen_platform_only_snapshot_requires_a_new_local_build(regional_source, tmp_path, monkeypatch, capsys, region):
    import sys

    source_db, fleet, mapping = regional_source
    payload = report_module().build_report(source_db, region=region, fleet_snapshot=fleet, market_mapping=mapping)
    payload["hk_testcase_analysis"]["scope"]["classification"] = "regional_platform_execution"
    output = tmp_path / ("legacy_" + region.lower())
    output.mkdir()
    snapshot = output / (region + "_Test_Output_2025_2026.json")
    snapshot.write_text(json.dumps(payload), encoding="utf-8")
    before = snapshot.read_bytes()
    monkeypatch.setattr(sys, "argv", ["report", "--region", region, "--refresh-platform-types", "--output", str(output)])

    with pytest.raises(SystemExit) as error:
        report_module().main()

    assert error.value.code == 2
    assert "testcase-to-MR" in capsys.readouterr().err
    assert snapshot.read_bytes() == before
    assert not (output / (region + "_Test_Output_2025_2026.html")).exists()


def test_supplemental_fleet_snapshot_cli_refreshes_only_mapping_and_retains_source_cutoff(regional_source, tmp_path, monkeypatch, capsys):
    import sys

    module = report_module()
    source_db, fleet, mapping = regional_source
    payload = module.build_report(source_db, region="CN", fleet_snapshot=fleet, market_mapping=mapping)
    output = tmp_path / "cn_fleet_lookup"
    output.mkdir()
    snapshot = output / "CN_Test_Output_2025_2026.json"
    snapshot.write_text(json.dumps(payload), encoding="utf-8")
    supplement = tmp_path / "supplemental_fleet.json"
    supplement.write_text(json.dumps({"fetched_at": "2026-11-10T06:49:39.215Z", "platforms": [
        {"ids": {"vin": "WBA11LE030HY56855", "vNumber": "345678"}},
    ]}), encoding="utf-8")
    before = source_db.read_bytes()

    def reject_database_build(*args, **kwargs):
        raise AssertionError("Mapping refresh must not rebuild execution data")

    monkeypatch.setattr(module, "build_report", reject_database_build)
    monkeypatch.setattr(sys, "argv", ["report", "--region", "CN", "--refresh-platform-types", "--output", str(output),
                                    "--fleet-snapshot", str(fleet), "--supplemental-fleet-snapshot", str(supplement)])

    module.main()

    capsys.readouterr()
    result = json.loads(snapshot.read_text(encoding="utf-8"))
    records = {row["run_id"]: row for row in result["hk_testcase_analysis"]["runs"]}
    assert records["R12"]["actual_environment"] == "Vehicle"
    assert records["R12"]["v_numbers"] == ["345678"]
    assert result["hk_testcase_analysis"]["overview"] == payload["hk_testcase_analysis"]["overview"]
    assert result["source"]["latest_fetched_at"] == payload["source"]["latest_fetched_at"]
    assert result["classification_refresh"]["source_database_queried"] is False
    assert result["classification_refresh"]["supplemental_fleet_snapshots"] == [str(supplement)]
    by_vin = {row["vin"]: row for row in result["v_number_mapping"]}
    assert by_vin[CN_VIN]["v_number"] == "123456"
    assert by_vin["WBA11LE030HY56855"]["mapping_snapshot_at"] == "2026-11-10T06:49:39.215Z"
    assert source_db.read_bytes() == before
    monkeypatch.setattr(sys, "argv", ["report", "--region", "CN", "--refresh-platform-types", "--output", str(output),
                                    "--fleet-snapshot", str(fleet)])
    module.main()
    capsys.readouterr()
    repeated = json.loads(snapshot.read_text(encoding="utf-8"))
    assert repeated["classification_refresh"]["supplemental_fleet_snapshots"] == [str(supplement)]
    assert next(row for row in repeated["hk_testcase_analysis"]["runs"] if row["run_id"] == "R12")["actual_environment"] == "Vehicle"


def test_saved_native_snapshot_cli_reuses_local_records_without_reading_database(regional_source, tmp_path, monkeypatch, capsys):
    import hashlib
    import sys

    module = report_module()
    source_db, fleet, mapping = regional_source
    payload = module.build_report(source_db, region="TW", fleet_snapshot=fleet, market_mapping=mapping)
    payload["supplemental_read"] = {"status": "authentication_required", "metadata_probe_http_status": 401}
    output = tmp_path / "cached_tw"
    output.mkdir()
    (output / "TW_Test_Output_2025_2026.json").write_text(json.dumps(payload), encoding="utf-8")
    source_snapshot = tmp_path / "saved_hk_native.json"
    source_snapshot.write_text(json.dumps({"fetched_at": "2026-11-02T03:15:58Z", "records": [
        {"id": "R9", "test": {"id": "T-TW"}, "testplatformid_udf": TW_VIN},
        {"id": "OUTSIDE-TW", "test": {"id": "OTHER"}, "testplatformid_udf": HK_VIN},
    ]}), encoding="utf-8")
    before = source_db.read_bytes()

    def reject_database_build(*args, **kwargs):
        raise AssertionError("Frozen cached-snapshot refresh must not build from the database")

    monkeypatch.setattr(module, "build_report", reject_database_build)
    monkeypatch.setattr(sys, "argv", ["report", "--region", "TW", "--refresh-platform-types", "--output", str(output),
                                    "--fleet-snapshot", str(fleet), "--cached-mr-platform-snapshot", str(source_snapshot)])

    module.main()

    capsys.readouterr()
    result = json.loads((output / "TW_Test_Output_2025_2026.json").read_text(encoding="utf-8"))
    assert result["native_mr_platform_enrichment"]["read_mode"] == "saved_native_snapshot"
    assert result["native_mr_platform_enrichment"]["source_snapshot_file"] == str(source_snapshot)
    assert result["native_mr_platform_enrichment"]["source_snapshot_sha256"] == hashlib.sha256(source_snapshot.read_bytes()).hexdigest()
    assert result["native_mr_platform_enrichment"]["fetched_at"] == "2026-11-02T03:15:58Z"
    assert result["supplemental_read"]["native_mr_records_read"] == 0
    assert result["supplemental_read"]["local_native_mr_records_reused"] == 1
    assert result["supplemental_read"]["previous_native_read_attempt"]["metadata_probe_http_status"] == 401
    assert source_db.read_bytes() == before


def test_hong_kong_calendar_boundary(source_db):
    connection = sqlite3.connect(source_db)
    connection.execute("UPDATE octane_manual_runs SET finished='2024-12-31T16:30:00Z' WHERE mr_id='R6'")
    connection.commit()
    connection.close()
    payload = report_module().build_report(source_db)
    run = next(row for row in payload["runs"] if row["run_id"] == "R6")
    assert run["year"] == 2025
    assert run["date"].startswith("2025-01-01T00:30:00")


def test_aida_businesskey_is_used_when_name_has_no_embedded_code():
    code, name, original = report_module()._aida({
        "top_aida": "Voice operation", "aida_businesskey": "01.04.02.01.01.05",
    })
    assert code == "01.04.02.01.01.05"
    assert name == original == "Voice operation"


def test_export_consistency_and_spreadsheet_formula_safety(source_db, tmp_path):
    from openpyxl import load_workbook

    module = report_module()
    payload = module.build_report(source_db)
    payload["defects"][0]["name"] = "=1+1"
    outputs = module.write_report(payload, tmp_path / "report")
    exported = json.loads(outputs["json"].read_text(encoding="utf-8"))
    assert exported["overview"] == payload["overview"]
    workbook = load_workbook(outputs["excel"], read_only=True, data_only=False)
    assert len(sheet_records(workbook, "MR Details", in_vin_scope=True)) == len(payload["runs"])
    assert workbook["Defects"].max_row == len(payload["defects"]) + 1
    assert not any(cell.data_type == "f" for row in workbook["Defects"] for cell in row)
    workbook.close()
    assert "application/json" in outputs["html"].read_text(encoding="utf-8")
    assert "AIDA" in outputs["markdown"].read_text(encoding="utf-8")
    assert "'=1+1" in (outputs["json"].parent / "defects.csv").read_text(encoding="utf-8-sig")


def test_native_testcase_body_csv_preserves_frozen_execution_fields(tmp_path):
    module = report_module()
    path = tmp_path / "testcases.csv"
    link = "https://octane.example/ui/?p=1002/2001#/entity-navigation?entityType=test_manual&id=1001033"
    original = {"test_id": link, "test_name": "[HK] Search", "run_count": "32",
                "mt_description": "", "mt_steps": "", "mt_steps_num": "32",
                "octane_mt_link_bare": link}
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(original))
        writer.writeheader()
        writer.writerow(original)
    script = '- Setup: use an HK account\n- Search for "Hong Kong, Central"\n- ?Expected: matching destination appears\n'
    snapshot = {"fetched_at": "2026-11-03T09:00:00Z", "records": [{
        "test": {"id": "1001033", "subtype": "test_manual", "description": "<p>Search destination</p>",
                 "last_modified": "2026-10-01T09:00:00Z"},
        "script": {"script": script, "test_version": 4, "version_stamp": 8,
                   "last_modified": "2026-10-01T09:00:00Z"},
    }]}

    summary = module.enrich_testcase_csv(path, snapshot)

    with path.open(encoding="utf-8-sig", newline="") as handle:
        records = list(csv.DictReader(handle))
    assert len(records) == 1
    assert records[0]["mt_steps"].startswith("'")
    assert records[0]["mt_steps"].removeprefix("'") == script
    assert records[0]["mt_description"] == "<p>Search destination</p>"
    assert {key: records[0][key] for key in original if key not in {"mt_description", "mt_steps"}} == {
        key: value for key, value in original.items() if key not in {"mt_description", "mt_steps"}
    }
    assert records[0]["mt_body_fetched_at"] == snapshot["fetched_at"]
    assert records[0]["mt_script_test_version"] == "4"
    assert records[0]["mt_steps_status"] == "available"
    assert summary["scripts_populated"] == 1
    exported = path.read_bytes()
    module.enrich_testcase_csv(path, snapshot)
    assert path.read_bytes() == exported


@pytest.mark.parametrize("invalid", ["unexpected_id", "duplicate_id", "missing_id", "missing_script", "missing_timestamp"])
def test_native_testcase_body_csv_rejects_invalid_snapshots_before_writing(tmp_path, invalid):
    module = report_module()
    path = tmp_path / "testcases.csv"
    path.write_text("test_id,mt_steps\n1001033,original\n", encoding="utf-8-sig")
    original = path.read_bytes()
    record = {"test": {"id": "1001033", "subtype": "test_manual", "description": ""},
              "script": {"script": "- Search\n- ?Expected: destination appears"}}
    snapshot = {"fetched_at": "2026-11-03T09:00:00Z", "records": [record]}
    if invalid == "unexpected_id":
        record["test"]["id"] = "1001267"
    elif invalid == "duplicate_id":
        snapshot["records"].append(record)
    elif invalid == "missing_id":
        snapshot["records"] = []
    elif invalid == "missing_script":
        record["script"] = None
    else:
        snapshot.pop("fetched_at")

    with pytest.raises(ValueError):
        module.enrich_testcase_csv(path, snapshot)

    assert path.read_bytes() == original


def test_native_testcase_body_csv_marks_native_empty_scripts_and_escapes_formulas(tmp_path):
    module = report_module()
    path = tmp_path / "testcases.csv"
    path.write_text("test_id,mt_description,mt_steps,mt_steps_num\n1001033,,,0\n", encoding="utf-8-sig")
    snapshot = {"fetched_at": "2026-11-03T09:00:00Z", "records": [{
        "test": {"id": "1001033", "subtype": "test_manual", "description": "=1+1"},
        "script": {"script": ""},
    }]}

    summary = module.enrich_testcase_csv(path, snapshot)

    with path.open(encoding="utf-8-sig", newline="") as handle:
        record = next(csv.DictReader(handle))
    assert record["mt_description"] == "'=1+1"
    assert record["mt_steps"] == ""
    assert record["mt_steps_status"] == "native_empty"
    assert record["mt_steps_num"] == "0"
    assert summary["native_empty_scripts"] == 1
    assert summary["scripts_populated"] == 0


def test_native_testcase_body_csv_retains_unavailable_cases_with_explicit_http_status(tmp_path):
    module = report_module()
    path = tmp_path / "testcases.csv"
    path.write_text("test_id,mt_description,mt_steps,mt_steps_num\n1001033,,,32\n784680,,,9\n", encoding="utf-8-sig")
    snapshot = {"fetched_at": "2026-11-03T09:00:00Z", "records": [{
        "test": {"id": "1001033", "subtype": "test_manual", "description": ""},
        "script": {"script": "- Search\n- ?Expected: destination appears"},
    }], "errors": [{"id": "784680", "endpoint": "tests/784680", "error_type": "HTTPError",
                   "http_status": 404, "script_http_status": 404}]}

    summary = module.enrich_testcase_csv(path, snapshot)

    with path.open(encoding="utf-8-sig", newline="") as handle:
        records = list(csv.DictReader(handle))
    assert len(records) == 2
    assert records[0]["mt_steps_status"] == "available"
    assert records[1]["test_id"] == "784680"
    assert records[1]["mt_steps"] == ""
    assert records[1]["mt_steps_num"] == "9"
    assert records[1]["mt_steps_status"] == "native_unavailable"
    assert records[1]["mt_body_http_status"] == "404"
    assert summary["scripts_populated"] == 1
    assert summary["unavailable_bodies"] == 1
    assert summary["complete"] is False


def test_missing_defect_vin_uses_latest_history_without_reviving_clear_or_overriding_current(source_db):
    connection = sqlite3.connect(source_db)
    connection.execute("CREATE TABLE octane_defect_history_events (defect_id TEXT, field_name TEXT, event_timestamp TEXT, new_value TEXT)")
    connection.executemany("INSERT INTO octane_defects (defect_id,name,vin,creation_time,year,top_aida,market) VALUES (?,?,?,?,?,?,?)", [
        ("D5", "Recover missing VIN", "", "2025-05-01T10:00:00Z", 2025, AIDA, "HK"),
        ("D6", "Explicitly cleared VIN", "", "2025-05-01T10:00:00Z", 2025, AIDA, "HK"),
        ("D7", "HK override without market flag", "", "2026-05-01T10:00:00Z", 2026, AIDA, "CN"),
    ])
    connection.executemany("INSERT INTO octane_defect_history_events VALUES (?,?,?,?)", [
        ("D4", "vin_udf", "2025-05-01T11:00:00Z", HK_VIN),
        ("D5", "vin_udf", "2025-05-01T11:00:00Z", HK_VIN),
        ("D6", "vin_udf", "2025-05-01T11:00:00Z", HK_VIN),
        ("D6", "vin_udf", "2025-05-02T11:00:00Z", ""),
        ("D7", "vin_udf", "2026-05-01T11:00:00Z", HK_VIN),
    ])
    connection.commit()
    connection.close()
    payload = report_module().build_report(source_db)
    records = {row["defect_id"]: row for row in payload["defects"]}
    assert set(records) == {"D1", "D2", "D5", "D7"}
    assert records["D5"]["vin_source"] == "history.latest_vin"
    assert records["D5"]["vin_current"] == ""
    assert records["D5"]["vin_history_at"] == "2025-05-01T11:00:00Z"
    assert records["D7"]["matched_vins"] == [HK_VIN]


def test_quality_view_explicitly_labels_source_wide_scope():
    template = report_module().HTML_TEMPLATE
    assert 'id="table-scope"' in template
    assert "Source-wide quality: year scope only; not limited to HK VINs." in template


def test_html_filters_keep_all_native_option_values():
    from html.parser import HTMLParser

    class SelectParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.select_id = None
            self.options = {}

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if tag == "select":
                self.select_id = attributes.get("id")
                self.options[self.select_id] = []
            elif tag == "option" and self.select_id:
                self.options[self.select_id].append(attributes.get("value"))

        def handle_endtag(self, tag):
            if tag == "select":
                self.select_id = None

    parser = SelectParser()
    parser.feed(report_module().HTML_TEMPLATE)
    assert parser.options["year"] == ["all", "2025", "2026"]
    assert parser.options["evidence"] == ["all", "user_confirmed", "legacy_mapping_candidate"]


def test_detected_by_is_not_presented_as_test_method():
    template = report_module().HTML_TEMPLATE
    assert "'Detected By'" in template
    assert "'Discovery Method'" not in template


@pytest.fixture
def title_source_db(source_db):
    connection = sqlite3.connect(source_db)
    connection.execute("CREATE TABLE octane_testcases (test_id TEXT,test_name TEXT,scope_team TEXT,scope_release TEXT,raw_json TEXT)")
    connection.executemany("INSERT INTO octane_testcases VALUES (?,?,?,?,?)", [
        ("T1", "[Speech][HK] Basic", "DTSV_China", "R-25-03", '{"synthesized":true}'),
        ("T1", "[Speech][HK] Basic variant", "DTSV_China", "R-26-03", '{"synthesized":true}'),
        ("T3", "[HK] Planned only", "DTSV_China", "R-26-04", "{}"),
        ("T4", "[HK] Historical only", "DTSV_China", "R-25-04", "{}"),
        ("T5", "XHKY General", "DTSV_China", "R-26-04", "{}"),
        ("T6", "Hong Kong No run", "DTSV_China", "R-26-04", "{}"),
    ])
    connection.executemany("INSERT INTO octane_manual_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("R9", "T4", "[HK] Historical only", "Passed", "", "{}", "2024-06-01T10:00:00Z", "", "2025-01-01T00:00:00Z", 2025, AIDA, ""),
        ("R10", "T5", "XHKY General", "Passed", "", "{}", "2026-06-01T10:00:00Z", "", "2026-06-01T09:00:00Z", 2026, AIDA, ""),
        ("R11", "T7", "[Contact][HK] From run title", "Failed", "", "{}", "2026-06-01T10:00:00Z", "", "2026-06-01T09:00:00Z", 2026, AIDA, "D1"),
    ])
    connection.commit()
    connection.close()
    return source_db


def test_hk_title_classification_matches_explicit_markers_only():
    module = report_module()
    for title in ("[Contact][HK] Search", "hk / phone", "Hong Kong speech", "China_HK", "[HK/TW] Shared"):
        assert module.has_hk_title(title), title
    for title in ("XHKY", "Hongkongish", "Speech CN", "Traditional Chinese", "Cantonese"):
        assert not module.has_hk_title(title), title


def test_hk_title_cohort_keeps_full_catalog_and_non_hk_or_missing_vehicle_runs(title_source_db):
    payload = report_module().build_report(title_source_db)
    analysis = payload["hk_testcase_analysis"]
    assert {row["test_id"] for row in analysis["cases"]} == {"T1", "T3", "T4", "T6", "T7"}
    assert {row["run_id"] for row in analysis["runs"]} == {"R1", "R2", "R3", "R4", "R5", "R11"}
    assert analysis["overview"]["title_marked_testcases"] == 5
    assert analysis["overview"]["catalog_backed_testcases"] == 4
    assert analysis["overview"]["run_title_only_testcases"] == 1
    assert analysis["overview"]["run_records"] == 6
    assert analysis["overview"]["executed_runs"] == 5
    assert analysis["overview"]["planned_runs"] == 1
    assert analysis["overview"]["executed_testcases"] == 2
    assert analysis["overview"]["cases_without_period_records"] == 2
    assert analysis["overview"]["cases_without_period_execution"] == 3
    assert payload["overview"]["executed_runs"] == 4
    assert analysis["overlap_with_vin_scope"]["run_records"] == 3
    assert analysis["overlap_with_vin_scope"]["executed_runs"] == 2


def test_hk_title_case_latest_status_and_aida_totals(title_source_db):
    analysis = report_module().build_report(title_source_db)["hk_testcase_analysis"]
    cases = {row["test_id"]: row for row in analysis["cases"]}
    assert len(cases["T1"]["title_variants"]) >= 2
    assert cases["T1"]["executed_runs"] == 4
    assert cases["T1"]["latest_executed_status"] == "Failed"
    assert cases["T3"]["latest_record_status"] == "Planned"
    assert cases["T3"]["latest_executed_status"] == "No execution"
    assert cases["T4"]["latest_record_status"] == "No record"
    assert cases["T7"]["classification_sources"] == ["run.test_name"]
    assert sum(row["executed_runs"] for row in analysis["aida_summary"]) == 5
    assert sum(row["run_records"] for row in analysis["year_summary"]) == 6
    assert analysis["latest_executed_status_counts"] == {"Failed": 2, "No execution": 3}


def test_hk_case_analysis_is_exported_in_all_report_formats(title_source_db, tmp_path):
    import csv
    from openpyxl import load_workbook

    module = report_module()
    payload = module.build_report(title_source_db)
    outputs = module.write_report(payload, tmp_path / "integrated")
    workbook = load_workbook(outputs["excel"], read_only=True)
    assert workbook["HK Cases"].max_row == 6
    assert len(sheet_records(workbook, "MR Details", in_testcase_cohort=True)) == 6
    assert len(sheet_records(workbook, "Annual", cohort="testcase")) == 2
    assert "AIDA" in workbook.sheetnames
    assert all("latest_executed_status" in row for row in sheet_records(workbook, "HK Cases"))
    workbook.close()
    with (outputs["json"].parent / "testcases.csv").open(encoding="utf-8-sig", newline="") as handle:
        assert len(list(csv.DictReader(handle))) == 5
    with (outputs["json"].parent / "executions.csv").open(encoding="utf-8-sig", newline="") as handle:
        assert sum(row["in_testcase_cohort"] == "True" for row in csv.DictReader(handle)) == 6
    assert "HK-Marked Testcase Analysis" in outputs["markdown"].read_text(encoding="utf-8")
    template = outputs["html"].read_text(encoding="utf-8")
    assert 'id="cohort-title"' in template
    assert 'id="case-status"' in template
    assert 'id="cohort-vin"' in template


def test_case_analysis_is_computed_once_not_per_defect(title_source_db, monkeypatch):
    module = report_module()
    original = module._hk_testcase_analysis
    calls = []

    def tracked(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "_hk_testcase_analysis", tracked)
    module.build_report(title_source_db)
    assert len(calls) == 1


def test_case_analysis_exists_without_vin_matched_defects(title_source_db):
    connection = sqlite3.connect(title_source_db)
    connection.execute("DELETE FROM octane_defects")
    connection.commit()
    connection.close()
    payload = report_module().build_report(title_source_db)
    assert payload["overview"]["defect_count"] == 0
    assert payload["hk_testcase_analysis"]["overview"]["executed_runs"] == 5


def test_title_cohort_uses_link_associations_not_zero_vin_defect_metrics(title_source_db):
    payload = report_module().build_report(title_source_db)
    analysis = payload["hk_testcase_analysis"]
    assert analysis["overview"]["linked_defects"] == 1
    assert analysis["overview"]["defect_count"] is None
    assert all(row["defect_count"] is None for row in analysis["aida_summary"])
    assert all(row["defect_count"] is None for row in analysis["year_summary"])
    assert payload["overview"]["defect_count"] == 2


def test_environment_signals_are_not_physical_execution_proof():
    module = report_module()
    car = "dWKP (car - dyn. subsystem test platform)"
    rack = "sWKP (rack - static subsystem test platform)"
    assert module.environment_tag_signal(car) == "vehicle_tag_only"
    assert module.environment_tag_signal(rack) == "bench_tag_only"
    assert module.environment_tag_signal(car + "," + rack) == "both_tags"
    assert module.environment_tag_signal("") == "no_platform_tag"
    assert module.environment_tag_signal("Other category") == "other_taxonomy"


def test_environment_audit_keeps_dual_tags_mutually_exclusive(title_source_db):
    connection = sqlite3.connect(title_source_db)
    connection.execute("ALTER TABLE octane_manual_runs ADD COLUMN taxonomies TEXT")
    connection.execute("ALTER TABLE octane_manual_runs ADD COLUMN testing_tool_type TEXT")
    connection.execute("UPDATE octane_manual_runs SET taxonomies='dWKP (car),sWKP (rack)',testing_tool_type='Manual Runner' WHERE mr_id='R1'")
    connection.execute("UPDATE octane_manual_runs SET taxonomies='dWKP (car)' WHERE mr_id='R2'")
    connection.execute("UPDATE octane_manual_runs SET taxonomies='sWKP (rack)' WHERE mr_id='R3'")
    connection.commit()
    connection.close()
    payload = report_module().build_report(title_source_db)
    analysis = payload["hk_testcase_analysis"]
    audit = analysis["environment_comparison"]
    assert len(audit["evidence"]) == len(analysis["runs"]) == 6
    assert sum(row["run_records"] for row in audit["signal_summary"]) == 6
    signals = {row["environment_tag_signal"]: row for row in audit["signal_summary"]}
    assert signals["both_tags"]["run_records"] == 1
    assert signals["vehicle_tag_only"]["run_records"] == 1
    assert signals["bench_tag_only"]["run_records"] == 1
    assert audit["actual_comparison_ready"] is False
    assert audit["actual_bench_executions"] == 2
    assert audit["actual_vehicle_executions"] == 0
    assert audit["matched_case_version_comparison"] is None
    mapped = next(row for row in audit["evidence"] if row["run_id"] == "R1")
    assert mapped["vehicle_value"] == HK_VIN
    assert mapped["actual_environment"] == "Bench"
    assert mapped["v_numbers"] == ["CHUXIN"]


def test_environment_comparison_is_exported(title_source_db, tmp_path):
    from openpyxl import load_workbook

    module = report_module()
    payload = module.build_report(title_source_db)
    paths = module.write_report(payload, tmp_path / "environment")
    workbook = load_workbook(paths["excel"], read_only=True)
    records = sheet_records(workbook, "MR Details", in_testcase_cohort=True)
    assert len(records) == 6
    assert all("actual_environment" in row and "environment_tag_signal" in row for row in records)
    assert "Rack Vehicle AIDA" in workbook.sheetnames
    assert "Platform Capabilities" in workbook.sheetnames
    workbook.close()
    assert (paths["json"].parent / "executions.csv").is_file()
    assert not (paths["json"].parent / "environment_evidence.csv").exists()
    assert "Bench / Vehicle Comparison" in paths["markdown"].read_text(encoding="utf-8")
    assert 'id="environment-view"' in paths["html"].read_text(encoding="utf-8")


def test_html_payload_excludes_unused_audit_copies_without_changing_frozen_data(title_source_db):
    module = report_module()
    payload = module.build_report(title_source_db)
    original = json.dumps(payload, sort_keys=True)

    embedded = module.build_html_payload(payload)

    assert "vin_scope_candidate_runs" not in embedded
    assert "runs" not in embedded
    assert "testcases" not in embedded
    assert "environment_comparison" not in embedded
    for collection, fields in (("runs", ("run_id", "test_id", "status", "date", "date_basis", "software_version", "linked_defect_ids")),
                               ("cases", ("test_id", "test_name", "aida_names"))):
        before = payload["hk_testcase_analysis"][collection]
        after = embedded["hk_testcase_analysis"][collection]
        assert [{key: row.get(key) for key in fields} for row in after] == [{key: row.get(key) for key in fields} for row in before]
    assert [row["defect_id"] for row in embedded["defects"]] == [row["defect_id"] for row in payload["defects"]]
    assert "evidence" not in embedded["hk_testcase_analysis"]["environment_comparison"]
    assert all("environment_classification_rule" not in row for row in embedded["hk_testcase_analysis"]["runs"])
    assert all("native_field_scope" not in row for row in embedded["hk_testcase_analysis"]["runs"])
    assert json.dumps(payload, sort_keys=True) == original


def test_compact_exports_keep_all_records_and_scopes_without_duplicate_files(title_source_db, tmp_path):
    import csv
    from openpyxl import load_workbook

    module = report_module()
    payload = module.build_report(title_source_db)
    primary = {row["run_id"] for row in payload["hk_testcase_analysis"]["runs"]}
    platform = {row["run_id"] for row in payload["runs"]}
    output = tmp_path / "compact"
    output.mkdir()
    (output / "environment_evidence.csv").write_text("obsolete generated copy\n", encoding="utf-8")
    (output / "custom_notes.csv").write_text("user notes\n", encoding="utf-8")
    (output / "HK_TestCase_Export_with_Octane_Links.csv").write_text("saved native bodies\n", encoding="utf-8")
    (output / "ANALYSIS.md").write_text("# Prior report\n\n## Native Testcase Body Supplement: 2026-11-03\n\nNative script evidence retained.\n\n## Obsolete generated section\n\nobsolete summary\n", encoding="utf-8")

    paths = module.write_report(payload, output)

    workbook = load_workbook(paths["excel"], read_only=True)
    assert "MR Details" in workbook.sheetnames
    assert "Environment Evidence" not in workbook.sheetnames
    assert "HK Case Runs" not in workbook.sheetnames
    assert len(workbook.sheetnames) <= 25
    assert workbook["HK Cases"].max_row - 1 == len(payload["hk_testcase_analysis"]["cases"])
    workbook.close()
    with (output / "executions.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert {row["run_id"] for row in rows} == primary | platform
    assert {row["run_id"] for row in rows if row["in_testcase_cohort"] == "True"} == primary
    assert {row["run_id"] for row in rows if row["in_vin_scope"] == "True"} == platform
    assert len(rows) == len(primary | platform)
    assert "native_field_scope" not in rows[0]
    with (output / "data_dictionary.csv").open(encoding="utf-8-sig", newline="") as handle:
        definitions = list(csv.DictReader(handle))
    assert any(row["column"] == "native_field_scope" and row["kind"] == "constant" for row in definitions)
    assert not (output / "environment_evidence.csv").exists()
    assert (output / "custom_notes.csv").read_text(encoding="utf-8") == "user notes\n"
    assert (output / "HK_TestCase_Export_with_Octane_Links.csv").read_text(encoding="utf-8") == "saved native bodies\n"
    assert len(payload["export_layout"]["csv_files"]) <= 16
    assert "Native script evidence retained." in paths["markdown"].read_text(encoding="utf-8")
    assert "obsolete summary" not in paths["markdown"].read_text(encoding="utf-8")
    import re
    for target in re.findall(r"\]\(([^()\s]+\.csv)\)", paths["markdown"].read_text(encoding="utf-8")):
        assert (output / target).is_file(), target


def test_render_only_reuses_frozen_records_without_source_or_mapping_reads(title_source_db, tmp_path, monkeypatch, capsys):
    import sys

    module = report_module()
    output = tmp_path / "render_only"
    payload = module.build_report(title_source_db)
    paths = module.write_report(payload, output)
    frozen = json.loads(paths["json"].read_text(encoding="utf-8"))
    def forbidden(*args, **kwargs):
        raise AssertionError("Render-only must not read or reclassify source evidence")
    for symbol in ("build_report", "load_v_number_mapping", "refresh_platform_types"):
        monkeypatch.setattr(module, symbol, forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(sys, "argv", ["report", "--render-only", "--output", str(output)])

    module.main()

    result = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert result["hk_testcase_analysis"]["runs"] == frozen["hk_testcase_analysis"]["runs"]
    assert result["hk_testcase_analysis"]["cases"] == frozen["hk_testcase_analysis"]["cases"]
    assert result["defects"] == frozen["defects"]
    assert result["publication_refresh"]["input_mode"] == "frozen_render_only"
    assert json.loads(capsys.readouterr().out)["source_queried_for_rendering"] is False


def test_v_number_rule_distinguishes_named_benches_from_numeric_vehicles():
    module = report_module()
    for value in ("chuxin", " HULUWA ", "CHNKNB", "DALIAN", "ZHUDI0"):
        assert module.classify_v_number(value) == "Bench"
    for value in ("553620", "137178"):
        assert module.classify_v_number(value) == "Vehicle"
    for value in ("", "N/A", "Unknown", "V137178", "WBA32JM0X0HY52877"):
        assert module.classify_v_number(value) == "Unknown"


def test_tw_screenshot_v_numbers_override_fleet_without_changing_snapshot(tmp_path):
    module = report_module()
    expected = {
        "WBA81HM020HY56858": ("YUNNAN", "Bench"),
        "WBA11KE03W5648172": ("374385", "Vehicle"),
        "WBA25GP040HY25272": ("XINZHU", "Bench"),
        "WMW11GA010HY26704": ("WANWAN", "Bench"),
        "WBACV6105KLK20040": ("546668", "Vehicle"),
        "WBA31KF020HY60753": ("ZHUHAI", "Bench"),
        "WBY21CF08PCN23000": ("558077", "Vehicle"),
        "WBA31HR030HY44656": ("BROLLY", "Bench"),
        "SCA51HA070HY58560": ("MARIAO", "Bench"),
        "WBA21HY030HY47120": ("CNG70R", "Bench"),
    }
    fleet = tmp_path / "fleet.json"
    fleet.write_text(json.dumps({"fetched_at": "2026-09-04T02:46:05Z", "platforms": [
        {"ids": {"vin": "WBA81HM020HY56858", "vNumber": "123456"}},
    ]}), encoding="utf-8")
    before = fleet.read_bytes()

    mappings = module.load_v_number_mapping(fleet)

    by_vin = {row["vin"]: row for row in mappings}
    for vin, (number, environment) in expected.items():
        assert by_vin[vin]["v_number"] == number
        assert by_vin[vin]["actual_environment"] == environment
        assert by_vin[vin]["v_number_source"] == "user_confirmed_tw_screenshot_2026-11-09"
        assert by_vin[vin]["mapping_snapshot_at"] == "2026-11-09"
        assert module._platform_identity(vin, mappings)["actual_environment"] == environment
    assert by_vin["WBA81HM020HY56858"]["supplemental_snapshot_v_number"] == "123456"
    assert "123456" not in by_vin["WBA81HM020HY56858"]["v_number_aliases"]
    assert sum(environment == "Bench" for number, environment in expected.values()) == 7
    assert sum(environment == "Vehicle" for number, environment in expected.values()) == 3
    assert fleet.read_bytes() == before


def test_v_number_mapping_overrides_taxonomy_but_never_fills_missing_identifiers():
    module = report_module()
    bench = module._environment_fields({"testplatformid": HK_VIN, "taxonomies": "dWKP (car)"}, {})
    assert bench["actual_environment"] == "Bench"
    assert bench["v_numbers"] == ["CHUXIN"]
    assert bench["environment_classification_source"] == "vin_to_v_number_user_rule"
    car = module._environment_fields({"testplatformid": "WBY22CF0X0CN18562", "taxonomies": "sWKP (rack)"}, {})
    assert car["actual_environment"] == "Vehicle"
    assert car["v_numbers"] == ["553620"]
    named = module._environment_fields({"testplatformid": "huluwa"}, {})
    assert named["actual_environment"] == "Bench"
    assert module._environment_fields({"taxonomies": "dWKP (car)"}, {})["actual_environment"] == "Unknown"
    assert module._environment_fields({"testplatformid": CN_VIN}, {})["actual_environment"] == "Unknown"


def test_mixed_platform_identifiers_remain_unresolved():
    module = report_module()
    data = module._environment_fields({"testplatformid": HK_VIN + ";WBY22CF0X0CN18562"}, {})
    assert data["actual_environment"] == "Unknown"
    assert data["environment_classification_source"] == "conflicting_v_number_types"


def test_mapped_platform_type_classifies_defects_without_changing_total(source_db):
    payload = report_module().build_report(source_db)
    assert payload["overview"]["defect_count"] == 2
    assert all(row["actual_environment"] == "Bench" for row in payload["defects"])
    comparison = payload["platform_type_summary"]
    assert sum(row["defect_count"] for row in comparison) == 2


def test_supplemental_fleet_mapping_retains_base_rows_and_original_observation_dates(tmp_path):
    module = report_module()
    base = tmp_path / "base_fleet.json"
    base.write_text(json.dumps({"fetched_at": "2026-09-04T00:00:00Z", "platforms": [
        {"ids": {"vin": CN_VIN, "vNumber": "111111"}},
        {"ids": {"vin": "WBA11LE030HY56855", "vNumber": "222222"}},
    ]}), encoding="utf-8")
    supplement = tmp_path / "looked_up_fleet.json"
    supplement.write_text(json.dumps({"fetched_at": "2026-11-10T06:49:39.215Z", "platforms": [
        {"ids": {"vin": CN_VIN, "vNumber": "SPECTR"}},
        {"ids": {"vin": TW_VIN, "vNumber": "999999"}},
    ]}), encoding="utf-8")
    before = base.read_bytes(), supplement.read_bytes()

    mappings = module.load_v_number_mapping(base, supplemental_fleet_paths=[supplement])

    by_vin = {row["vin"]: row for row in mappings}
    assert by_vin[CN_VIN]["v_number"] == "SPECTR"
    assert by_vin[CN_VIN]["actual_environment"] == "Bench"
    assert by_vin[CN_VIN]["v_number_source"] == str(supplement)
    assert by_vin[CN_VIN]["mapping_snapshot_at"] == "2026-11-10T06:49:39.215Z"
    assert by_vin["WBA11LE030HY56855"]["v_number"] == "222222"
    assert by_vin["WBA11LE030HY56855"]["mapping_snapshot_at"] == "2026-09-04T00:00:00Z"
    assert by_vin[TW_VIN]["v_number"] == "YUNNAN"
    assert by_vin[TW_VIN]["v_number_source"] == "user_confirmed_tw_screenshot_2026-11-09"
    assert (base.read_bytes(), supplement.read_bytes()) == before


def test_fleet_v_number_mapping_uses_user_confirmed_names_and_keeps_mixed_codes_unknown(tmp_path):
    module = report_module()
    fleet = tmp_path / "fleet.json"
    fleet.write_text(json.dumps({"fetched_at": "2026-09-04T00:00:00Z", "platforms": [
        {"ids": {"vin": HK_VIN, "vNumber": "999999"}},
        {"ids": {"vin": "WBA32HR020DA03337", "vNumber": "144670"}},
        {"ids": {"vin": "WBA42HZ0X0HY47121", "vNumber": "HKTW71"}},
    ]}), encoding="utf-8")
    mappings = module.load_v_number_mapping(fleet)
    records = {row["vin"]: row for row in mappings}
    assert records[HK_VIN]["v_number"] == "CHUXIN"
    assert records["WBA32HR020DA03337"]["actual_environment"] == "Vehicle"
    assert records["WBA42HZ0X0HY47121"]["actual_environment"] == "Unknown"


def test_user_confirmed_weihai_and_hktw70_resolve_without_llm(tmp_path):
    module = report_module()
    assert module.classify_v_number("HKTW70") == "Bench"
    assert module.classify_v_number("hktw70") == "Bench"
    assert module.classify_v_number("HKTW71") == "Unknown"
    fleet = tmp_path / "fleet_confirmed.json"
    fleet.write_text(json.dumps({"fetched_at": "2026-09-04T00:00:00Z", "platforms": [
        {"ids": {"vin": "WBA42HZ0X0HY47121", "vNumber": "HKTW70"}},
    ]}), encoding="utf-8")
    mappings = module.load_v_number_mapping(fleet)
    records = {row["vin"]: row for row in mappings}
    assert records["WBA62FX000HY29503"]["v_number"] == "WEIHAI"
    assert records["WBA62FX000HY29503"]["v_number_source"] == "user_confirmed_2026-11-02"
    assert module._platform_identity("WBA62FX000HY29503", mappings)["actual_environment"] == "Bench"
    assert module._platform_identity("WEIHAI", mappings)["actual_environment"] == "Bench"
    assert module._platform_identity("WBA42HZ0X0HY47121", mappings)["actual_environment"] == "Bench"


def test_user_confirmed_cng70r_and_two_prefixed_vehicle_numbers():
    module = report_module()
    assert module.classify_v_number("CNG70R") == "Bench"
    assert module.classify_v_number("V547984") == "Vehicle"
    assert module.classify_v_number("V718601") == "Vehicle"
    assert module.classify_v_number("V137178") == "Unknown"
    mappings = module.load_v_number_mapping()
    by_vin = {row["vin"]: row for row in mappings}
    assert by_vin["WBACR620X09F64155"]["v_number"] == "V547984"
    assert by_vin["WBA12EH000CJ07626"]["v_number"] == "V718601"
    for vin in ("WBACR620X09F64155", "WBA12EH000CJ07626"):
        assert module._platform_identity(vin, mappings)["actual_environment"] == "Vehicle"
        assert by_vin[vin]["v_number_source"] == "user_confirmed_2026-11-02"


def test_confirmed_553623_identity_does_not_automatically_confirm_hk_region():
    module = report_module()
    mappings = module.load_v_number_mapping()
    records = {row["vin"]: row for row in mappings}
    assert records["WBY21CF02PCN18309"]["v_number"] == "553623"
    assert module._platform_identity("WBY21CF02PCN18309", mappings)["actual_environment"] == "Vehicle"
    assert module._platform_identity("553623", mappings)["actual_environment"] == "Vehicle"
    assert "WBY21CF02PCN18309" not in {row["vin"] for row in module._inventory()}


def test_report_views_combine_overview_but_keep_execution_and_defect_evidence_distinct(title_source_db):
    module = report_module()
    payload = module.build_report(title_source_db)
    before = json.dumps(payload, sort_keys=True)
    views = module.build_report_views(payload)
    assert json.dumps(payload, sort_keys=True) == before
    assert views["hk_overview"]["testcase_ids"] == 5
    assert views["hk_overview"]["executed_runs"] == 5
    assert views["hk_overview"]["defect_count"] == 2
    assert views["hk_overview"]["defects_are_testcase_discoveries"] is False


def test_rack_vehicle_comparison_uses_hk_case_executions_not_only_vin_cohort(title_source_db):
    module = report_module()
    payload = module.build_report(title_source_db)
    case_runs = payload["hk_testcase_analysis"]["runs"]
    next(row for row in case_runs if row["run_id"] == "R4")["actual_environment"] = "Vehicle"
    comparison = module.build_report_views(payload)["platform_comparison"]
    rows = {row["actual_environment"]: row for row in comparison["summary"]}
    assert rows["Bench"]["executed_runs"] == 2
    assert rows["Vehicle"]["executed_runs"] == 1
    assert rows["Vehicle"]["management_origin"] == "Dynamic-origin"
    assert rows["Vehicle"]["actual_vehicle_motion"] == "Unknown"
    assert rows["Vehicle"]["defect_count"] == 0
    assert comparison["unclassified_executed_runs"] == 2
    assert comparison["same_case_version_equivalence_verified"] is False


def test_two_report_entry_points_are_task_focused_and_exported(title_source_db, tmp_path):
    module = report_module()
    paths = module.write_report(module.build_report(title_source_db), tmp_path / "clear_views")
    exported = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert "report_views" in exported
    template = paths["html"].read_text(encoding="utf-8")
    assert "HK Testcases &amp; Defects</button>" in template
    assert "Rack vs Vehicle</button>" in template
    assert "HK-marked testcases</button>" not in template
    assert "HK VIN platforms</button>" not in template
    assert 'value="comparison"' in template


def test_platform_classification_refresh_does_not_read_changed_source_database(source_db, monkeypatch):
    module = report_module()
    payload = module.build_report(source_db)
    original_overview = dict(payload["overview"])

    def forbidden_connection(*args, **kwargs):
        pytest.fail("classification refresh must not query the live source")

    monkeypatch.setattr(sqlite3, "connect", forbidden_connection)
    result = module.refresh_platform_types(payload, module.load_v_number_mapping())
    assert result["overview"] == original_overview
    assert result["source"]["source_read_for_classification_refresh"] is False
    assert all(row["actual_environment"] == "Bench" for row in result["defects"])
    assert sum(row["defect_count"] for row in result["platform_type_summary"]) == original_overview["defect_count"]


def test_v_number_classification_appears_in_report_exports(source_db, tmp_path):
    from openpyxl import load_workbook

    module = report_module()
    payload = module.refresh_platform_types(module.build_report(source_db), module.load_v_number_mapping())
    paths = module.write_report(payload, tmp_path / "classified")
    workbook = load_workbook(paths["excel"], read_only=True)
    assert {row["cohort"] for row in sheet_records(workbook, "Rack vs Vehicle")} == {"testcase", "regional_platform"}
    assert "V-number Mapping" in workbook.sheetnames
    workbook.close()
    markdown = paths["markdown"].read_text(encoding="utf-8")
    assert "User-confirmed V-number rule" in markdown
    assert "Actual bench/vehicle counts, pass-rate comparison and same-case/version outcome differences are unassessed" not in markdown
    html = paths["html"].read_text(encoding="utf-8")
    assert '<option value="actual">' in html
    assert 'V-number' in html


def test_classification_refresh_separates_frozen_data_from_changed_live_source(source_db):
    module = report_module()
    payload = module.build_report(source_db)
    payload["source"]["mtime_ns_before"] = -1
    result = module.refresh_platform_types(payload, module.load_v_number_mapping())
    assert result["source"]["database_matches_frozen_snapshot_at_refresh"] is False
    assert result["source"]["unchanged_at_data_build"] is True
    assert result["source"]["unchanged"] is None
    assert result["source"]["source_modified_by_classification_refresh"] is False
    evidence = next(row for row in result["environment_comparison"]["evidence"] if row["run_id"] == "R1")
    assert evidence["v_number_mapping_sources"] == ["user_confirmed_screenshot_vin_mapping"]


def test_management_origin_never_proves_motion_or_automation_feasibility(title_source_db):
    module = report_module()
    payload = module.build_report(title_source_db)
    before = json.dumps(payload, sort_keys=True)
    analysis = module.build_automation_analysis(payload)
    assert json.dumps(payload, sort_keys=True) == before
    origins = {row["actual_environment"]: row for row in analysis["source_summary"]}
    assert origins["Bench"]["management_origin"] == "Static-origin"
    assert origins["Vehicle"]["management_origin"] == "Dynamic-origin"
    assert all(row["actual_vehicle_motion"] == "Unknown" for row in analysis["source_summary"])
    assert len(analysis["cases"]) == 5
    assert all(row["automation_feasible"] is None for row in analysis["cases"])
    assert all(row["review_status"] == "Unreviewed" for row in analysis["cases"])
    assert analysis["observed_savings_hours"] is None
    assert analysis["cases"][0]["test_id"] == "T1"


def test_physical_title_signals_do_not_mean_manual_only(title_source_db):
    module = report_module()
    payload = module.build_report(title_source_db)
    payload["hk_testcase_analysis"]["cases"][0]["test_name"] = "[HK] Speech while driving with road noise"
    result = module.build_automation_analysis(payload)
    candidate = next(row for row in result["cases"] if row["test_id"] == payload["hk_testcase_analysis"]["cases"][0]["test_id"])
    assert candidate["review_route"] == "Physical-context review"
    assert candidate["automation_feasible"] is None
    assert candidate["actual_vehicle_motion"] == "Unknown"
    assert "replay" in candidate["candidate_approach"].lower()


def test_effort_scenario_requires_inputs_and_counts_setup_once():
    module = report_module()
    assert module.effort_scenario()["net_scenario_hours_saved"] is None
    result = module.effort_scenario(execution_count=100, manual_minutes=10, adoption_fraction=0.5,
                                    supervision_minutes=2, build_hours=3, maintenance_hours=1, validation_hours=1)
    assert result["manual_baseline_hours"] == pytest.approx(16.67, abs=0.01)
    assert result["automated_scenario_hours"] == pytest.approx(15.0, abs=0.01)
    assert result["net_scenario_hours_saved"] == pytest.approx(1.67, abs=0.01)
    assert result["break_even_executions"] == pytest.approx(75)
    assert result["is_measured_saving"] is False


def test_effort_scenario_preserves_negative_savings_and_validates_share():
    module = report_module()
    result = module.effort_scenario(execution_count=10, manual_minutes=2, adoption_fraction=1,
                                    supervision_minutes=3, build_hours=2, maintenance_hours=1, validation_hours=0)
    assert result["net_scenario_hours_saved"] < 0
    assert result["break_even_executions"] is None
    with pytest.raises(ValueError):
        module.effort_scenario(execution_count=10, manual_minutes=2, adoption_fraction=1.2,
                              supervision_minutes=1, build_hours=0, maintenance_hours=0, validation_hours=0)


def test_automation_review_and_blank_effort_model_are_exported(title_source_db, tmp_path):
    from openpyxl import load_workbook

    module = report_module()
    paths = module.write_report(module.build_report(title_source_db), tmp_path / "automation")
    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert len(payload["automation_analysis"]["cases"]) == 5
    assert payload["automation_analysis"]["effort_scenario"]["net_scenario_hours_saved"] is None
    workbook = load_workbook(paths["excel"], read_only=True)
    assert workbook["Automation Candidates"].max_row == 6
    assert "Automation Gates" in workbook.sheetnames
    assert "Automation Routes" in workbook.sheetnames
    assert "Capacity Assumptions" in workbook.sheetnames
    workbook.close()
    assert (paths["json"].parent / "automation_candidates.csv").is_file()
    assert "Automation Opportunity Review" in paths["markdown"].read_text(encoding="utf-8")
    template = paths["html"].read_text(encoding="utf-8")
    assert 'id="automation-view"' in template
    assert 'id="effort-volume"' in template


def test_missing_local_mr_identifier_is_not_reported_as_unlinked_native_run():
    module = report_module()
    missing = module.mr_platform_capture_info({"vehicle_value": "", "vin_source": "missing"})
    assert missing["mr_platform_capture_status"] == "Not captured in local MR snapshot"
    assert missing["native_mr_link_status"] == "Not checked against current Octane MR"
    assert missing["native_mr_platform_field"] == "testplatformid_udf"
    captured = module.mr_platform_capture_info({"vehicle_value": "553620", "vin_source": "testplatformid"})
    assert captured["mr_platform_capture_status"] == "Platform identifier captured locally"
    assert captured["native_mr_link_status"] == "Not checked against current Octane MR"


def test_report_discloses_manual_run_capture_gap_not_missing_testcase_vehicle_link(title_source_db, tmp_path):
    from openpyxl import load_workbook

    module = report_module()
    paths = module.write_report(module.build_report(title_source_db), tmp_path / "mr_capture")
    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    audit = payload["mr_platform_capture_audit"]
    assert audit["native_platform_field"] == "testplatformid_udf"
    assert audit["native_mr_linkage_checked"] is False
    assert all("mr_platform_capture_status" in row for row in payload["hk_testcase_analysis"]["runs"])
    assert sum(row["run_records"] for row in audit["summary"]) == 6
    workbook = load_workbook(paths["excel"], read_only=True)
    assert any(row["scope"] == "report_native_capture" for row in sheet_records(workbook, "Data Quality"))
    workbook.close()
    assert "MR platform data capture is incomplete" in paths["html"].read_text(encoding="utf-8")
    assert "source runs have no vehicle identifier" not in paths["html"].read_text(encoding="utf-8")


def test_saved_native_snapshot_selects_matching_mrs_and_preserves_observation_time(title_source_db):
    module = report_module()
    payload = module.build_report(title_source_db)
    original = json.dumps(payload, sort_keys=True)
    snapshot = {"fetched_at": "2026-11-02T03:15:58Z", "snapshot_file": "saved_hk_native.json", "records": [
        {"id": "R3", "test": {"id": "T1"}, "testplatformid_udf": HK_VIN},
        {"id": "NOT-IN-REPORT", "test": {"id": "OTHER"}, "testplatformid_udf": CN_VIN},
    ]}

    selected = module.select_saved_mr_platform_snapshot(payload, snapshot)

    assert [row["id"] for row in selected["records"]] == ["R3"]
    assert selected["fetched_at"] == snapshot["fetched_at"]
    assert selected["read_mode"] == "saved_native_snapshot"
    assert selected["source_snapshot_file"] == "saved_hk_native.json"
    assert selected["source_snapshot_records"] == 2
    payload["supplemental_read"] = {"status": "authentication_required", "metadata_probe_http_status": 401}
    result = module.enrich_native_mr_platform_links(payload, selected)
    assert result["native_mr_platform_enrichment"]["read_mode"] == "saved_native_snapshot"
    assert result["native_mr_platform_enrichment"]["source_snapshot_file"] == "saved_hk_native.json"
    assert result["supplemental_read"]["status"] == "saved_native_mr_snapshot_reused"
    assert result["supplemental_read"]["native_mr_records_read"] == 0
    assert result["supplemental_read"]["local_native_mr_records_reused"] == 1
    assert result["supplemental_read"]["previous_native_read_attempt"]["metadata_probe_http_status"] == 401
    assert result["hk_testcase_analysis"]["overview"] == payload["hk_testcase_analysis"]["overview"]
    payload.pop("supplemental_read")
    assert json.dumps(payload, sort_keys=True) == original


def test_saved_backfill_preserves_row_times_source_write_and_unavailable_mrs(title_source_db):
    module = report_module()
    payload = module.build_report(title_source_db)
    unavailable = next(row for row in payload["hk_testcase_analysis"]["runs"] if row["run_id"] != "R3")
    snapshot = {"fetched_at": "2026-11-10T08:00:00Z", "source_database_updated": True,
                "source_backfill_result": {"updated_nonempty": 1}, "records": [
                    {"id": "R3", "test": {"id": "T1"}, "testplatformid_udf": HK_VIN,
                     "observed_at": "2026-11-10T07:00:00Z"}],
                "errors": [{"id": unavailable["run_id"], "reason": "not_returned_by_native_query"},
                           {"id": "OUTSIDE", "reason": "not_returned_by_native_query"}]}
    selected = module.select_saved_mr_platform_snapshot(payload, snapshot)
    result = module.enrich_native_mr_platform_links(payload, selected)
    row = next(row for row in result["hk_testcase_analysis"]["runs"] if row["run_id"] == "R3")
    assert row["platform_identifier_observed_at"] == "2026-11-10T07:00:00Z"
    metadata = result["native_mr_platform_enrichment"]
    assert metadata["source_database_updated"] is False
    assert metadata["source_snapshot_database_updated"] is True
    assert metadata["source_snapshot_backfill_result"] == {"updated_nonempty": 1}
    assert metadata["unavailable_mr_records"] == 1
    missing = next(row for row in result["hk_testcase_analysis"]["runs"] if row["run_id"] == unavailable["run_id"])
    assert missing["native_platform_lookup_error"] == "not_returned_by_native_query"
    assert "not returned" in missing["native_mr_link_status"].lower()


@pytest.mark.parametrize("invalid", ["duplicate_mr", "missing_test_id", "wrong_test_id", "missing_timestamp"])
def test_saved_native_snapshot_rejects_invalid_matching_evidence(title_source_db, invalid):
    module = report_module()
    payload = module.build_report(title_source_db)
    record = {"id": "R3", "test": {"id": "T1"}, "testplatformid_udf": HK_VIN}
    snapshot = {"fetched_at": "2026-11-02T03:15:58Z", "records": [record]}
    if invalid == "duplicate_mr":
        snapshot["records"].append(record)
    elif invalid == "missing_test_id":
        record["test"] = {}
    elif invalid == "wrong_test_id":
        record["test"]["id"] = "OTHER"
    else:
        snapshot.pop("fetched_at")

    with pytest.raises(ValueError):
        module.select_saved_mr_platform_snapshot(payload, snapshot)


def test_native_mr_link_enrichment_preserves_frozen_business_counts(title_source_db):
    module = report_module()
    payload = module.build_report(title_source_db)
    before = json.dumps(payload, sort_keys=True)
    links = {"fetched_at": "2026-11-02T10:00:00Z", "records": [
        {"id": "R3", "test": {"id": "T1"}, "testplatformid_udf": "WBY22CF0X0CN18562", "equipment_udf": {"data": []}},
    ]}
    result = module.enrich_native_mr_platform_links(payload, links)
    assert json.dumps(payload, sort_keys=True) == before
    assert result["frozen_vin_scope_overview"] == payload["overview"]
    assert result["overview"]["executed_runs"] == payload["overview"]["executed_runs"] + 1
    assert result["hk_testcase_analysis"]["overview"] == payload["hk_testcase_analysis"]["overview"]
    row = next(row for row in result["hk_testcase_analysis"]["runs"] if row["run_id"] == "R3")
    assert row["vehicle_value"] == "WBY22CF0X0CN18562"
    assert row["frozen_platform_identifier"] == ""
    assert row["native_platform_lookup"] == "Fetched"
    assert module.mr_platform_capture_info(row)["mr_platform_capture_status"] == "Identifier retrieved from native MR"
    assert row["actual_environment"] == "Vehicle"
    audit = module._mr_capture_audit(result)
    enriched = next(row for row in result["hk_testcase_analysis"]["runs"] if row["run_id"] == "R3")
    assert enriched["frozen_mr_platform_capture_status"] == "Not captured in frozen local MR snapshot"
    assert enriched["historical_mr_link_verified"] is False
    assert audit["historical_platform_backfill_performed"] is False
    assert audit["current_native_platform_supplement_performed"] is True


def test_native_mr_link_enrichment_rejects_mismatched_testcase(title_source_db):
    module = report_module()
    payload = module.build_report(title_source_db)
    with pytest.raises(ValueError):
        module.enrich_native_mr_platform_links(payload, {"fetched_at": "2026-11-02T10:00:00Z", "records": [
            {"id": "R3", "test": {"id": "OTHER"}, "testplatformid_udf": "WBY22CF0X0CN18562"},
        ]})


def test_native_mr_links_rebuild_vin_cohort_and_all_derived_totals(title_source_db):
    module = report_module()
    payload = module.build_report(title_source_db)
    snapshot = {"fetched_at": "2026-11-02T10:00:00Z", "records": [
        {"id": "R3", "test": {"id": "T1"}, "testplatformid_udf": "WBY22CF0X0CN18562"},
        {"id": "R4", "test": {"id": "T1"}, "testplatformid_udf": CN_VIN},
    ]}
    result = module.enrich_native_mr_platform_links(payload, snapshot)
    assert {row["run_id"] for row in result["runs"]} == {"R1", "R2", "R3", "R5", "R7", "R8"}
    assert result["overview"]["run_records"] == 6
    assert result["overview"]["executed_runs"] == 5
    assert result["overview"]["defect_count"] == payload["overview"]["defect_count"]
    assert sum(row["executed_runs"] for row in result["year_summary"]) == 5
    assert sum(row["executed_runs"] for row in result["month_summary"]) == 5
    assert sum(row["executed_runs"] for row in result["aida_summary"]) == 5
    assert result["hk_testcase_analysis"]["overview"] == payload["hk_testcase_analysis"]["overview"]
    assert result["hk_testcase_analysis"]["overlap_with_vin_scope"]["executed_runs"] == 3
    repeated = module.enrich_native_mr_platform_links(result, snapshot)
    assert repeated["overview"] == result["overview"]
    assert len({row["run_id"] for row in repeated["runs"]}) == 6


def test_numeric_v_number_identifier_can_resolve_hk_vin_membership(title_source_db):
    module = report_module()
    payload = module.build_report(title_source_db)
    result = module.enrich_native_mr_platform_links(payload, {"fetched_at": "2026-11-02T10:00:00Z", "records": [
        {"id": "R3", "test": {"id": "T1"}, "testplatformid_udf": "553620"},
    ]})
    record = next(row for row in result["runs"] if row["run_id"] == "R3")
    assert record["matched_vins"] == ["WBY22CF0X0CN18562"]
    assert record["actual_environment"] == "Vehicle"


def test_unknown_platform_followup_distinguishes_empty_identifier_from_unmapped_v_number():
    module = report_module()
    rows = [
        {"run_id": "MR-EMPTY", "test_id": "T1", "vehicle_value": "", "actual_environment": "Unknown", "native_platform_lookup": "Fetched", "is_executed": True, "v_numbers": []},
        {"run_id": "MR-MAP", "test_id": "T2", "vehicle_value": "WBA62FX000HY29503", "actual_environment": "Unknown", "is_executed": True, "v_numbers": []},
        {"run_id": "MR-MIXED", "test_id": "T3", "vehicle_value": "WBA42HZ0X0HY47121", "actual_environment": "Unknown", "is_executed": False, "v_numbers": ["HKTW71"]},
        {"run_id": "MR-CAR", "test_id": "T4", "vehicle_value": "553620", "actual_environment": "Vehicle", "is_executed": True},
    ]
    result = module.unknown_platform_followup(rows)
    assert len(result) == 3
    assert sum(row["run_records"] for row in result) == 3
    by_id = {row["platform_identifier"]: row for row in result}
    assert by_id["<EMPTY>"]["unknown_reason"] == "Native MR identifier returned empty"
    assert by_id["WBA62FX000HY29503"]["unknown_reason"] == "VIN to V-number mapping unavailable"
    assert by_id["WBA42HZ0X0HY47121"]["unknown_reason"] == "V-number type requires user confirmation"
    assert all(row["user_confirmed_platform_type"] == "" for row in result)


@pytest.mark.parametrize("region", ["HK", "TW", "CN"])
@pytest.mark.parametrize("use_saved_snapshot", [False, True])
def test_stacked_charts_show_execution_status_and_defect_phase_in_both_sections(request, tmp_path, region, use_saved_snapshot):
    import shutil
    import subprocess
    from copy import deepcopy
    from pathlib import Path

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the exported chart DOM regression")
    module = report_module()
    if region == "HK":
        payload = module.build_report(request.getfixturevalue("title_source_db"))
        expected = {"overview_runs": 8, "overview_defects": 2, "blocked": 1, "phase06": 1,
                    "comparison_runs": 5, "comparison_defects": 2, "year_runs": 2, "year_defects": 1}
    else:
        source_db, fleet, mapping = request.getfixturevalue("regional_source")
        payload = module.build_report(source_db, region=region, fleet_snapshot=fleet, market_mapping=mapping)
        payload = module.refresh_platform_types(payload, module.load_v_number_mapping(fleet))
        expected = ({"overview_runs": 4, "overview_defects": 3, "blocked": 2, "phase06": 2,
                     "comparison_runs": 4, "comparison_defects": 2, "year_runs": 1, "year_defects": 0} if region == "TW" else
                    {"overview_runs": 7, "overview_defects": 2, "blocked": 1, "phase06": 1,
                     "comparison_runs": 3, "comparison_defects": 2, "year_runs": 3, "year_defects": 1})
    template = payload["hk_testcase_analysis"]["runs"][0]
    if use_saved_snapshot:
        selected = module.select_saved_mr_platform_snapshot(payload, {"fetched_at": "2026-11-02T03:15:58Z", "snapshot_file": "saved_native.json", "records": [
            {"id": template["run_id"], "test": {"id": template["test_id"]}, "testplatformid_udf": template["vehicle_value"]},
        ]})
        payload = module.enrich_native_mr_platform_links(payload, selected)
    for run_id, status in (("R-BLOCK", "Blocked"), ("R-ATTENTION", "Requires Attention")):
        record = deepcopy(template)
        record.update({"run_id": run_id, "status": status, "is_executed": status == "Requires Attention"})
        payload["hk_testcase_analysis"]["runs"].append(record)
        if region != "HK":
            payload["runs"].append(deepcopy(record))
    paths = module.write_report(payload, tmp_path / "stacked")
    script = r"""
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {JSDOM, VirtualConsole} from 'jsdom';
const expected=JSON.parse(process.argv[3]);
const errors=[];
const console=new VirtualConsole();
console.on('jsdomError',error=>errors.push(error.message));
const dom=new JSDOM(readFileSync(process.argv[2],'utf8'),{runScripts:'dangerously',virtualConsole:console});
const document=dom.window.document;
const total=selector=>[...document.querySelectorAll(selector+' .stack-row')].reduce((sum,row)=>sum+Number(row.dataset.total),0);
assert.equal(errors.length,0,errors.join('; '));
const nativeSupplement=JSON.parse(document.querySelector('#report-data').textContent).native_mr_platform_enrichment;
const notice=document.querySelector('#quality-warning').textContent;
if(nativeSupplement?.read_mode==='saved_native_snapshot'){
 assert.ok(notice.includes('reused from local saved native'));
 assert.ok(notice.includes('2026-11-02T03:15:58Z'));
 assert.ok(notice.includes('No new Octane requests'));
}else{
 assert.equal(notice.includes('Actual run platform links were supplemented from native MR metadata'),false,'A report without a native supplement must not claim native platform verification');
}
assert.equal(total('#aida-chart'),expected.overview_runs,'Overview must chart all MR result records, including planned and blocked');
assert.equal(total('#month-chart'),expected.overview_defects,'Overview must show defects without opening Defects');
assert.equal(Number(document.querySelector('#aida-chart .stack-segment[data-series="Blocked"]').dataset.value),expected.blocked);
assert.equal(document.querySelector('#aida-chart .stack-segment[data-series="Requires Attention"]').dataset.value,'1');
assert.equal(Number(document.querySelector('#month-chart .stack-segment[data-series="06"]').dataset.value),expected.phase06);
assert.ok(document.querySelector('#aida-chart .chart-legend [data-series="Blocked"]'));
assert.ok(document.querySelector('#month-chart .chart-legend [data-series="06"]'));
document.querySelector('#cohort-vin').click();
assert.equal(total('#aida-chart'),expected.comparison_runs,'Comparison must exclude unknown platform rows and include all result statuses');
assert.equal(total('#month-chart'),expected.comparison_defects,'Comparison must show rack/vehicle defect phases by default');
assert.ok(document.querySelector('#execution-detail-chart .stack-row'),'Execution AIDA comparison is present');
assert.ok(document.querySelector('#defect-detail-chart .stack-row'),'Defect AIDA comparison is present');
const year=document.querySelector('#year');year.value='2026';year.dispatchEvent(new dom.window.Event('change'));
assert.equal(total('#aida-chart'),expected.year_runs,'Year filter updates rack/vehicle execution stacks');
assert.equal(total('#month-chart'),expected.year_defects,'Year filter updates defect phase stacks');
assert.equal(errors.length,0,errors.join('; '));
const query=document.querySelector.bind(document);let selectionReads=0;
document.querySelector=selector=>{if(['#year','#vin'].includes(selector))selectionReads+=1;return query(selector);};
dom.window.selectedData();
document.querySelector=query;
assert.equal(selectionReads,2,'Platform filters must read year/VIN once per selection, not once per MR/defect');
const html=readFileSync(process.argv[2],'utf8'),opening='<script id="report-data" type="application/json">';
const dataStart=html.indexOf(opening)+opening.length,dataEnd=html.indexOf('</script>',dataStart);
const fullJson=readFileSync(process.argv[2].replace(/\.html$/,'.json'),'utf8').replaceAll('<','\\u003c');
const baseline=new JSDOM(html.slice(0,dataStart)+fullJson+html.slice(dataEnd),{runScripts:'dangerously',virtualConsole:console});
const reference=baseline.window.document;
function visibleSnapshot(doc){return ['.metrics','.table-wrap','#row-count','#page-count','#aida-chart','#month-chart','#execution-detail-chart','#defect-detail-chart','#quality-warning','#effort-panel','#hk-insights'].map(selector=>doc.querySelector(selector).textContent);}
function sameAfter(action){action(document);action(reference);assert.deepEqual(visibleSnapshot(document),visibleSnapshot(reference),'Projected data must preserve every visible view/filter result');}
function choose(doc,selector,value,event='change'){const control=doc.querySelector(selector);control.value=value;control.dispatchEvent(new doc.defaultView.Event(event));}
sameAfter(doc=>{choose(doc,'#year','all');doc.querySelector('#cohort-title').click();});
for(const cohort of ['#cohort-title','#cohort-vin']){
 sameAfter(doc=>doc.querySelector(cohort).click());
 for(const view of ['Testcases','AIDA','Annual','Monthly','Results','Runs','Environment','Defects','Platforms','Quality'])sameAfter(doc=>doc.querySelector(`[data-view="${view}"]`).click());
 sameAfter(doc=>doc.querySelector('[data-view="Environment"]').click());
 for(const option of document.querySelector('#environment-view').options)sameAfter(doc=>choose(doc,'#environment-view',option.value));
 for(const yearValue of ['2025','2026','all'])sameAfter(doc=>choose(doc,'#year',yearValue));
}
sameAfter(doc=>doc.querySelector('[data-view="Automation"]').click());
for(const option of document.querySelector('#automation-view').options)sameAfter(doc=>choose(doc,'#automation-view',option.value));
sameAfter(doc=>doc.querySelector('#cohort-title').click());
for(const status of ['Failed','Passed','No execution','all'])sameAfter(doc=>choose(doc,'#case-status',status));
sameAfter(doc=>choose(doc,'#search','HK','input'));
sameAfter(doc=>choose(doc,'#search','','input'));
sameAfter(doc=>choose(doc,'#page-size','100'));
sameAfter(doc=>choose(doc,'#page','2'));
assert.equal(errors.length,0,errors.join('; '));
baseline.window.close();
dom.window.close();
"""
    result = subprocess.run([node, "--input-type=module", "-", str(paths["html"]), json.dumps(expected)], input=script,
                            text=True, capture_output=True, cwd=Path(__file__).resolve().parents[2])
    assert result.returncode == 0, result.stdout + result.stderr


def test_capacity_insights_use_recent_year_and_disjoint_saving_lanes(title_source_db):
    module = report_module()
    payload = module.build_report(title_source_db)
    payload["source"]["latest_fetched_at"]["runs"] = "2026-10-20T09:53:01Z"
    payload["hk_testcase_analysis"]["cases"][0]["test_name"] = "[HK] Contact input matrix"
    before = json.dumps(payload, sort_keys=True)
    result = module.build_capacity_insights(payload)
    assert json.dumps(payload, sort_keys=True) == before
    assert result["planning_year"] == 2027
    assert result["planning_baseline"]["year"] == 2026
    assert result["planning_baseline"]["observed_executions"] == 2
    assert result["planning_baseline"]["calendar_days_observed"] == 293
    assert not set(result["automation_lane_run_ids"]) & set(result["rack_transfer_lane_run_ids"])
    assert result["person_capacity_hours_assumption"] == 1840
    assert result["one_person_replacement_proven"] is False
    assert result["realized_savings_hours"] is None
    assert len(result["scenarios"]) == 3
    assert all(row["is_measured_saving"] is False for row in result["scenarios"])
    for row in result["scenarios"]:
        assert row["combined_net_hours"] == pytest.approx(row["automation_net_hours"] + row["rack_transfer_net_hours"], abs=0.02)
        assert row["remaining_capacity_gap_hours"] == pytest.approx(max(0,1840-row["combined_net_hours"]), abs=0.02)


def test_capacity_insights_do_not_annualize_without_snapshot_date(title_source_db):
    module = report_module()
    result = module.build_capacity_insights(module.build_report(title_source_db))
    assert result["planning_baseline"]["annualized_automation_volume"] is None
    assert all(row["combined_net_hours"] is None for row in result["scenarios"])


def test_insights_replace_expanded_boundary_wall_and_are_exported(title_source_db, tmp_path):
    from openpyxl import load_workbook

    module = report_module()
    payload = module.build_report(title_source_db)
    payload["source"]["latest_fetched_at"]["runs"] = "2026-10-20T09:53:01Z"
    paths = module.write_report(payload, tmp_path / "capacity")
    exported = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert exported["capacity_insights"]["one_person_replacement_proven"] is False
    template = paths["html"].read_text(encoding="utf-8")
    assert 'id="hk-insights"' in template
    assert '<details id="counting-boundaries">' in template
    assert '<details id="counting-boundaries" open' not in template
    assert "HK Testing Insights and 2027 Capacity" in paths["markdown"].read_text(encoding="utf-8")
    workbook = load_workbook(paths["excel"], read_only=True)
    assert "HK Capacity Insights" in workbook.sheetnames
    assert workbook["2027 Capacity Scenarios"].max_row == 4
    assert "Capacity Pilot Queue" in workbook.sheetnames
    workbook.close()